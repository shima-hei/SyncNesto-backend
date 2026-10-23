"""実PostgreSQLと注入StorageでDLの境界・停止中コピーを検証する。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy import select, text, update

from app.core.config import settings
from app.db.session import session_local
from app.models.artifact_publication_access import (
    ArtifactPublicationGrant,
    ProjectGuestParticipation,
)
from app.models.session import UserSession
from app.models.tenant import TenantMember
from app.models.user import User
from app.routers.artifact_publication_access import download_service
from app.services.publication_delivery_storage import PublicationDeliveryStorage
from tests.fakes.storage import MemoryS3Client
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_preservations import advance, prepare
from tests.routers.test_artifact_preservations import context as context  # noqa: F401
from tests.routers.test_artifact_publications import prepare as prepare_publication
from tests.routers.test_artifact_publications import publication_base
from tests.routers.test_publication_access import publish, shared_path


class DeliveryS3(MemoryS3Client):
    """条件PUTと実形式の署名計算を持つ、ネットワークなしのfake。"""

    def __init__(self):
        super().__init__()
        self.on_put = None
        self.writes = 0
        self.signs = 0
        self.signer = boto3.client(
            "s3",
            endpoint_url="http://127.0.0.1:18333",
            aws_access_key_id="dummy",
            aws_secret_access_key="dummy-secret",
            region_name="ap-northeast-1",
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def put_object(self, **kwargs):
        if kwargs["Key"] in self.objects:
            raise ClientError(
                {
                    "Error": {"Code": "PreconditionFailed"},
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "PutObject",
            )
        self.writes += 1
        result = super().put_object(**kwargs)
        if self.on_put:
            self.on_put()
        return result

    def generate_presigned_url(self, method, **kwargs):
        self.signs += 1
        return self.signer.generate_presigned_url(method, **kwargs)


@pytest.fixture
def download_fixture(client, db, context, monkeypatch, create_test_user):
    """保全済み添付つき公開版と、別組織の本人2名を用意する。"""
    ctx = context
    preservation, _ = prepare(client, ctx)
    ready = advance(client, ctx, preservation)
    assert ready.status_code == 200 and ready.json()["status"] == "ready"
    base = publication_base(ctx)
    data = prepare_publication(client, base, ctx["release"], preservation["id"])
    created = client.post(base, json=data)
    assert created.status_code == 201
    ctx["publication"] = created.json()
    guests = []
    for i in range(2):
        person = create_test_user(email=f"dl-{i}@example.com")
        db.execute(
            update(TenantMember)
            .where(TenantMember.user_id == person.id)
            .values(status="removed")
        )
        person.user_type = "guest"
        db.commit()
        part = ProjectGuestParticipation(
            id=uuid4(),
            project_id=ctx["project"].id,
            user_id=person.id,
            generation=1,
            display_label=f"DL確認{i}",
            status="active",
            version=1,
            expires_at=datetime.now(UTC) + timedelta(days=7),
            created_by=ctx["user"].id,
        )
        db.add(part)
        db.commit()
        guests.append((person, part))
    ctx["guests"] = guests
    authorize_as(client, ctx["user"])
    publish(client, ctx)
    ctx["sid"] = authorize_as(client, guests[0][0])
    ctx["revision"] = client.get(shared_path(ctx)).json()["authorization_revision"]
    file = ctx["publication"]["content"]["files"][0]
    ctx["url"] = shared_path(ctx, f"/files/{file['id']}/download")
    ctx["request"] = {"expected_authorization_revision": ctx["revision"]}
    for name, value in {
        "shared_publication_download_enabled": True,
        "shared_file_delivery_endpoint_url": "http://127.0.0.1:18333",
        "shared_file_delivery_bucket_name": "dummy-delivery",
        "shared_file_delivery_access_key_id": "dummy",
        "shared_file_delivery_secret_access_key": "dummy-secret",
    }.items():
        monkeypatch.setattr(settings, name, value)
    delivery = DeliveryS3()
    ctx["delivery"] = delivery
    storage = PublicationDeliveryStorage(source=ctx["storage"], destination=delivery)
    monkeypatch.setattr(download_service, "storage_factory", lambda: storage)
    return ctx


def post(client, ctx):
    return client.post(ctx["url"], json=ctx["request"])


def test_download_selected_fixed_file_and_reuses_private_copy(
    client, db, download_fixture
):
    ctx = download_fixture
    first = post(client, ctx)
    assert first.status_code == 200
    data = first.json()
    assert set(data) == {"url", "expires_in", "expires_at", "authorization_revision"}
    assert 1 <= data["expires_in"] <= 60
    assert data["authorization_revision"] == ctx["revision"]
    assert first.headers["cache-control"] == "private, no-store"
    assert first.headers["referrer-policy"] == "no-referrer"
    assert list(ctx["delivery"].objects.values())[0][0] == ctx["body"]
    assert ctx["source"].storage_key not in data["url"]
    assert "response-cache-control=private" in data["url"]
    assert post(client, ctx).status_code == 200
    assert ctx["delivery"].writes == 1
    assert len(ctx["delivery"].objects) == 1
    assert (
        client.get(shared_path(ctx)).json()["authorization_revision"] == ctx["revision"]
    )


@pytest.mark.parametrize(
    "target", ["other_user", "unknown_file", "csrf", "revision", "flag"]
)
def test_download_denied_before_storage(client, download_fixture, monkeypatch, target):
    ctx = download_fixture
    if target == "other_user":
        authorize_as(client, ctx["guests"][1][0])
    elif target == "unknown_file":
        ctx["url"] = shared_path(ctx, f"/files/{uuid4()}/download")
    elif target == "csrf":
        client.headers.pop(settings.csrf_header_name)
    elif target == "revision":
        ctx["request"]["expected_authorization_revision"] = "0" * 64
    else:
        monkeypatch.setattr(settings, "shared_publication_download_enabled", False)
    expected = {
        "other_user": 404,
        "unknown_file": 404,
        "csrf": 403,
        "revision": 409,
        "flag": 403,
    }[target]
    response = post(client, ctx)
    assert response.status_code == expected
    assert response.headers["cache-control"] == "private, no-store"
    assert not ctx["delivery"].objects and ctx["delivery"].signs == 0


@pytest.mark.parametrize("target", ["grant", "session"])
def test_stopped_during_copy_never_signs(client, db, download_fixture, target):
    ctx = download_fixture

    def stop():
        if target == "grant":
            db.execute(text("SELECT publication_lock(:p)"), {"p": ctx["project"].id})
            grant = db.scalar(select(ArtifactPublicationGrant))
            grant.status = "revoked"
            grant.version += 1
            grant.revoked_at = datetime.now(UTC)
            grant.revocation_reason = "コピー中に停止"
        else:
            db.get(UserSession, ctx["sid"]).revoked_at = datetime.now(UTC)
        db.commit()

    ctx["delivery"].on_put = stop
    response = post(client, ctx)
    assert response.status_code == (404 if target == "grant" else 401)
    assert ctx["delivery"].signs == 0
    assert ctx["delivery"].objects


def test_corrupt_source_returns_unavailable_without_copy(client, download_fixture):
    ctx = download_fixture
    for key, (body, mime) in list(ctx["fake"].objects.items()):
        ctx["fake"].objects[key] = (b"x" * len(body), mime)
    response = post(client, ctx)
    assert response.status_code == 503
    assert response.json()["code"] == "SHARED_FILE_UNAVAILABLE"
    assert not ctx["delivery"].objects and ctx["delivery"].signs == 0


def test_copy_corruption_fails_closed_and_never_overwrites(client, download_fixture):
    ctx = download_fixture
    assert post(client, ctx).status_code == 200
    key = next(iter(ctx["delivery"].objects))
    ctx["delivery"].objects[key] = (b"bad", "application/octet-stream")
    response = post(client, ctx)
    assert response.status_code == 503
    assert ctx["delivery"].writes == 1 and ctx["delivery"].signs == 1


def test_session_remaining_bounds_real_signed_expiry(client, db, download_fixture):
    ctx = download_fixture
    expiry = datetime.now(UTC) + timedelta(seconds=6)
    db.get(UserSession, ctx["sid"]).absolute_expires_at = expiry
    db.commit()
    response = post(client, ctx)
    assert response.status_code == 200
    assert response.json()["expires_in"] <= 6
    assert datetime.fromisoformat(response.json()["expires_at"]) <= expiry


def test_input_cannot_select_storage_or_ttl(client, download_fixture):
    ctx = download_fixture
    response = client.post(
        ctx["url"], json={**ctx["request"], "storage_key": "other", "expires_in": 3600}
    )
    assert response.status_code == 422
    assert ctx["delivery"].signs == 0


def test_logout_commit_during_copy_is_seen_after_row_lock_wait(
    client, download_fixture
):
    """logoutの未commit更新を待ち、旧sessionから新URLを発行しない。"""
    ctx = download_fixture
    copied = Event()
    ctx["delivery"].on_put = copied.set
    with session_local() as stopper:
        stopper.execute(text("SET LOCAL lock_timeout='3s'"))
        stopper.execute(
            update(UserSession)
            .where(UserSession.id == ctx["sid"])
            .values(revoked_at=datetime.now(UTC))
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(post, client, ctx)
            try:
                assert copied.wait(5)
                assert not future.done()
            finally:
                stopper.commit()
            response = future.result(timeout=5)
    assert response.status_code == 401
    assert ctx["delivery"].signs == 0


def test_signing_holds_session_against_concurrent_logout(
    client, download_fixture, monkeypatch
):
    """発行を先に確定した場合だけ、logoutがその後へ並ぶ。"""
    ctx = download_fixture
    storage = download_service.storage_factory()
    sign = storage.sign
    entered, release, revoke_entered = Event(), Event(), Event()

    def gated(*args):
        entered.set()
        assert release.wait(5)
        return sign(*args)

    monkeypatch.setattr(storage, "sign", gated)

    def revoke():
        with session_local() as db:
            db.execute(text("SET LOCAL lock_timeout='3s'"))
            revoke_entered.set()
            db.execute(
                update(UserSession)
                .where(UserSession.id == ctx["sid"])
                .values(revoked_at=datetime.now(UTC))
            )
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        download = pool.submit(post, client, ctx)
        try:
            assert entered.wait(5)
            revoked = pool.submit(revoke)
            assert revoke_entered.wait(5)
            assert not revoked.done()
        finally:
            release.set()
        assert download.result(timeout=5).status_code == 200
        revoked.result(timeout=5)
    assert post(client, ctx).status_code == 401
    assert ctx["delivery"].signs == 1


def test_existing_user_row_writer_can_stop_without_lock_order_deadlock(
    client, download_fixture
):
    """既存User FOR UPDATE→資格境界と、新DLの順序が循環しない。"""
    ctx = download_fixture
    entered, copied, release = Event(), Event(), Event()
    ctx["delivery"].on_put = copied.set

    def writer():
        with session_local() as db:
            db.execute(text("SET LOCAL lock_timeout='3s'"))
            row = db.scalar(
                select(User).where(User.id == ctx["guests"][0][0].id).with_for_update()
            )
            assert row is not None
            entered.set()
            assert release.wait(5)
            row.is_active = False
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(writer)
        try:
            assert entered.wait(5)
            download = pool.submit(post, client, ctx)
            assert copied.wait(5)
        finally:
            release.set()
        writing.result(timeout=5)
        assert download.result(timeout=5).status_code in {401, 403, 404}
    assert ctx["delivery"].signs == 0
