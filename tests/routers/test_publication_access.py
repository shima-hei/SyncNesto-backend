"""実PostgreSQLで本人限定公開・失効・既存導線との分離を検証する。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from app.db.session import session_local
from app.models.artifact_publication import ArtifactPublication, ArtifactPublicationNode
from app.models.artifact_publication_access import (
    ArtifactPublicationEvent,
    ArtifactPublicationGrant,
    ProjectGuestParticipation,
)
from app.models.tenant import Tenant, TenantMember
from app.models.user import User
from app.repositories.user import UserRepository
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_publications import create, publication_base
from tests.routers.test_artifact_releases import context as context  # noqa: F401


@pytest.fixture
def access_fixture(client, db, context, create_test_user):
    """社内申請者と、通常所属を持たないダミー本人2名を用意する。"""
    publication, _ = create(client, context)
    guests = []
    for index in range(2):
        user = create_test_user(email=f"guest-{index}@example.com")
        db.execute(
            update(TenantMember)
            .where(TenantMember.user_id == user.id)
            .values(status="removed")
        )
        user.user_type = "guest"
        db.commit()
        part = ProjectGuestParticipation(
            id=uuid4(),
            project_id=context["project"].id,
            user_id=user.id,
            generation=1,
            display_label=f"外部確認{index + 1}",
            status="active",
            version=1,
            expires_at=datetime.now(UTC) + timedelta(days=7),
            created_by=context["user"].id,
        )
        db.add(part)
        db.commit()
        guests.append((user, part))
    authorize_as(client, context["user"])
    return dict(**context, publication=publication, guests=guests)


def publish(client, ctx, *, both=False, expires_at=None):
    """確認済み本文・版・世代で公開する。"""
    p = ctx["publication"]
    data = dict(
        request_id=str(uuid4()),
        expected_state="prepared",
        publication_version=1,
        public_digest=p["content"]["public_digest"],
        confirmed_full_content=True,
        recipients=[
            dict(
                participation_id=str(part.id),
                version=part.version,
                expires_at=(
                    expires_at or datetime.now(UTC) + timedelta(days=1)
                ).isoformat(),
            )
            for _, part in ctx["guests"][: 2 if both else 1]
        ],
    )
    url = f"{publication_base(ctx)}/{p['content']['id']}/publish"
    response = client.post(url, json=data)
    assert response.status_code == 200, response.text
    return response.json(), url, data


def shared_path(ctx, suffix=""):
    """公開UUIDだけをURLへ含める。"""
    return f"/shared/publications/{ctx['publication']['content']['id']}{suffix}"


def denied(client, ctx):
    """全shared取得が同じ404形で本文・添付を返さないことを確認する。"""
    expected = None
    for suffix in ["", "/nodes", "/files"]:
        response = client.get(shared_path(ctx, suffix))
        assert response.status_code == 404, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        if expected is None:
            expected = response.json()
        assert response.json() == expected
    listing = client.get("/shared/publications")
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 0 and listing.json()["items"] == []


def test_publish_same_content_guest_routes_and_current_state(
    client, db, access_fixture
):
    """内部編集を開放せず、同じ固定内容・全nodeを読める。"""
    ctx = access_fixture
    result, url, data = publish(client, ctx)
    assert result["state"] == "published" and result["version"] == 2
    row = client.get(f"{publication_base(ctx)}/{result['id']}").json()
    assert row["state"] == "published" and row["publication_version"] == 2
    assert row["content"] == ctx["publication"]["content"]
    replay = client.post(url, json=data)
    assert replay.status_code == 200 and replay.json() == result
    before = db.scalar(select(func.count()).select_from(ArtifactPublicationEvent))
    different = client.post(url, json={**data, "public_digest": "0" * 64})
    assert different.status_code == 409
    assert (
        db.scalar(select(func.count()).select_from(ArtifactPublicationEvent)) == before
    )
    authorize_as(client, ctx["guests"][0][0])
    metadata = client.get(shared_path(ctx))
    assert metadata.status_code == 200, metadata.text
    value = metadata.json()
    assert set(value) == {"content", "expires_at", "authorization_revision"}
    assert value["content"] == ctx["publication"]["content"]
    assert len(value["authorization_revision"]) == 64
    listing = client.get("/shared/publications").json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == result["id"]
    nodes = []
    cursor = None
    while True:
        response = client.get(
            shared_path(ctx, "/nodes"),
            params={"page_size": 1, **({"cursor": cursor} if cursor else {})},
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["authorization_revision"] == value["authorization_revision"]
        nodes.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(nodes) == ctx["publication"]["content"]["node_count"]
    assert [n["ordinal"] for n in nodes] == list(range(len(nodes)))
    assert client.get(shared_path(ctx, "/files")).json()["items"] == []
    assert client.get(publication_base(ctx)).status_code == 403
    assert client.get(ctx["base"]).status_code == 403
    assert client.get(f"/projects/{ctx['project'].id}/requirements").status_code == 403


def test_only_designated_recipient_and_uniform_notfound(client, access_fixture):
    """他本人・存在不明・未公開を同じ応答にし、検索件数も漏らさない。"""
    ctx = access_fixture
    publish(client, ctx)
    authorize_as(client, ctx["guests"][1][0])
    denied(client, ctx)
    actual = client.get(shared_path(ctx))
    unknown = client.get(f"/shared/publications/{uuid4()}")
    assert unknown.status_code == actual.status_code and unknown.json() == actual.json()


def test_revoke_one_preserves_other_and_no_history_revival(client, db, access_fixture):
    """片方の失効は他本人に影響せず、元の許可を復活できない。"""
    ctx = access_fixture
    publish(client, ctx, both=True)
    identity = ctx["publication"]["content"]["id"]
    grants = client.get(f"{publication_base(ctx)}/{identity}/grants").json()["items"]
    first = next(
        g for g in grants if g["participation_id"] == str(ctx["guests"][0][1].id)
    )
    response = client.post(
        f"{publication_base(ctx)}/{identity}/grants/{first['id']}/revoke",
        json={"request_id": str(uuid4()), "version": 1, "reason": "確認期間終了"},
    )
    assert response.status_code == 200, response.text
    authorize_as(client, ctx["guests"][0][0])
    denied(client, ctx)
    authorize_as(client, ctx["guests"][1][0])
    assert client.get(shared_path(ctx)).status_code == 200
    with pytest.raises(DBAPIError):
        db.execute(text("SELECT publication_lock(:p)"), {"p": ctx["project"].id})
        db.execute(
            update(ArtifactPublicationGrant)
            .where(ArtifactPublicationGrant.id == UUID(first["id"]))
            .values(status="active", version=3)
        )
        db.commit()
    db.rollback()


@pytest.mark.parametrize(
    "target",
    [
        "user",
        "project",
        "tenant",
        "membership",
        "requirement_document",
        "section",
        "requirement",
        "specification",
    ],
)
def test_direct_mutation_revokes_and_restore_does_not_republish(
    client, db, access_fixture, target
):
    """既存SQL/管理操作の変更が全取得を停止し、復元で公開を復活させない。"""
    ctx = access_fixture
    publish(client, ctx)
    person = ctx["guests"][0][0]
    if target == "user":
        db.execute(update(User).where(User.id == person.id).values(is_active=False))
        db.commit()
        db.execute(update(User).where(User.id == person.id).values(is_active=True))
        db.commit()
    elif target == "tenant":
        db.execute(
            update(Tenant)
            .where(Tenant.id == ctx["project"].tenant_id)
            .values(status="suspended")
        )
        db.commit()
        db.execute(
            update(Tenant)
            .where(Tenant.id == ctx["project"].tenant_id)
            .values(status="active")
        )
        db.commit()
    elif target == "membership":
        db.execute(
            update(TenantMember)
            .where(TenantMember.user_id == person.id)
            .values(status="active")
        )
        db.commit()
        db.execute(
            update(TenantMember)
            .where(TenantMember.user_id == person.id)
            .values(status="removed")
        )
        db.commit()
    else:
        row = ctx[
            {
                "project": "project",
                "requirement_document": "doc",
                "section": "section",
                "requirement": "req",
                "specification": "spec",
            }[target]
        ]
        row.deleted_at = datetime.now(UTC)
        db.commit()
        row.deleted_at = None
        db.commit()
    authorize_as(client, person)
    denied(client, ctx)
    assert (
        db.scalar(
            select(func.count())
            .select_from(ArtifactPublicationEvent)
            .where(ArtifactPublicationEvent.actor_kind == "system_guard")
        )
        > 0
    )


def test_withdraw_retains_fixed_body_and_rejects_republish(client, db, access_fixture):
    """公開全体の停止後も社内固定本文・証跡は保存される。"""
    ctx = access_fixture
    result, _, data = publish(client, ctx)
    identity = result["id"]
    count = db.scalar(select(func.count()).select_from(ArtifactPublicationNode))
    stop = client.post(
        f"{publication_base(ctx)}/{identity}/withdraw",
        json={"request_id": str(uuid4()), "version": 2, "reason": "公開範囲の見直し"},
    )
    assert stop.status_code == 200, stop.text
    assert (
        client.get(f"{publication_base(ctx)}/{identity}").json()["state"] == "withdrawn"
    )
    assert db.scalar(select(func.count()).select_from(ArtifactPublicationNode)) == count
    again = client.post(
        f"{publication_base(ctx)}/{identity}/publish",
        json={**data, "request_id": str(uuid4()), "publication_version": 3},
    )
    assert again.status_code == 409
    events = client.get(f"{publication_base(ctx)}/{identity}/events").json()
    assert events["total"] >= 4 and any(
        e["actor_kind"] == "user" for e in events["items"]
    )
    authorize_as(client, ctx["guests"][0][0])
    denied(client, ctx)


def test_atomic_recipients_stale_version_and_participation_scope(
    client, db, access_fixture
):
    """一人が不正なとき公開・許可・根イベントを一部だけ作らない。"""
    ctx = access_fixture
    p = ctx["publication"]
    part = ctx["guests"][0][1]
    data = dict(
        request_id=str(uuid4()),
        expected_state="prepared",
        publication_version=1,
        public_digest=p["content"]["public_digest"],
        confirmed_full_content=True,
        recipients=[
            dict(
                participation_id=str(part.id),
                version=2,
                expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
            )
        ],
    )
    before = db.scalar(select(func.count()).select_from(ArtifactPublicationEvent))
    response = client.post(
        f"{publication_base(ctx)}/{p['content']['id']}/publish", json=data
    )
    assert response.status_code == 409, response.text
    assert db.scalar(select(func.count()).select_from(ArtifactPublicationGrant)) == 0
    assert (
        db.scalar(select(func.count()).select_from(ArtifactPublicationEvent)) == before
    )
    assert (
        client.get(f"{publication_base(ctx)}/{p['content']['id']}").json()["state"]
        == "prepared"
    )


def test_same_organization_participation_is_rejected(client, db, context):
    """同組織の社内本人へguestを重ねない方針をDBでも守る。"""
    with pytest.raises(DBAPIError):
        db.add(
            ProjectGuestParticipation(
                id=uuid4(),
                project_id=context["project"].id,
                user_id=context["user"].id,
                generation=1,
                display_label="内部本人",
                status="active",
                version=1,
                expires_at=datetime.now(UTC) + timedelta(days=1),
                created_by=context["user"].id,
            )
        )
        db.commit()
    db.rollback()


def test_cursor_bound_to_session_and_history_immutable(client, db, access_fixture):
    """同じ本人の再login前cursorと業務履歴の書換えを拒否する。"""
    ctx = access_fixture
    publish(client, ctx)
    person = ctx["guests"][0][0]
    authorize_as(client, person)
    page = client.get(shared_path(ctx, "/nodes"), params={"page_size": 1}).json()
    assert page["next_cursor"]
    authorize_as(client, person)
    assert (
        client.get(
            shared_path(ctx, "/nodes"), params={"cursor": page["next_cursor"]}
        ).status_code
        == 409
    )
    with pytest.raises(DBAPIError):
        db.execute(update(ArtifactPublicationEvent).values(reason="改竄"))
        db.commit()
    db.rollback()


def await_database_wait(db, pattern):
    """実際のSQL待機を観測し、固定sleepで順序を推測しない。"""
    deadline = monotonic() + 4
    while monotonic() < deadline:
        db.execute(text("SELECT pg_stat_clear_snapshot()"))
        if db.scalar(
            text("""
            SELECT EXISTS(SELECT FROM pg_stat_activity
              WHERE pid<>pg_backend_pid() AND wait_event_type='Lock'
                AND query LIKE :pattern)
        """),
            {"pattern": pattern},
        ):
            return
    raise AssertionError("expected concurrent database boundary wait")


def test_actual_reader_waits_for_revocation_commit(client, db, access_fixture):
    """実shared APIが停止の後ろで待った場合に新しい状態で404になる。"""
    ctx = access_fixture
    publish(client, ctx)
    person = ctx["guests"][0][0]
    project_id = ctx["project"].id
    path = shared_path(ctx)
    authorize_as(client, person)
    with session_local() as stop, ThreadPoolExecutor(1) as pool:
        stop.execute(text("SELECT publication_lock(:p)"), {"p": project_id})
        stop.execute(
            update(ArtifactPublicationGrant)
            .where(ArtifactPublicationGrant.project_id == project_id)
            .values(
                status="revoked",
                version=ArtifactPublicationGrant.version + 1,
                revoked_at=datetime.now(UTC),
                revocation_reason="concurrent revocation",
            )
        )
        future = pool.submit(client.get, path)
        try:
            await_database_wait(db, "%publication_lock%")
        finally:
            stop.commit()
        response = future.result(timeout=5)
    assert response.status_code == 404, response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_actual_existing_user_row_lock_does_not_deadlock_publish(
    client, db, access_fixture
):
    """既存UserRepositoryの先行FOR UPDATEを保ち、新公開側だけで逆順を避ける。"""
    ctx = access_fixture
    actor_id = ctx["user"].id
    publication = ctx["publication"]
    part = ctx["guests"][0][1]
    payload = dict(
        request_id=str(uuid4()),
        expected_state="prepared",
        publication_version=1,
        public_digest=publication["content"]["public_digest"],
        confirmed_full_content=True,
        recipients=[
            dict(
                participation_id=str(part.id),
                version=1,
                expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
            )
        ],
    )
    url = f"{publication_base(ctx)}/{publication['content']['id']}/publish"
    with session_local() as legacy, ThreadPoolExecutor(1) as pool:
        UserRepository().get_by_id(legacy, actor_id, lock=True)
        future = pool.submit(client.post, url, json=payload)
        try:
            await_database_wait(db, "%FOR KEY SHARE%")
            legacy.execute(
                update(User).where(User.id == actor_id).values(is_active=False)
            )
        finally:
            legacy.commit()
        response = future.result(timeout=5)
    assert response.status_code in {401, 403}, response.text
    assert db.scalar(select(func.count()).select_from(ArtifactPublicationGrant)) == 0
    row = db.get(
        ArtifactPublication, UUID(publication["content"]["id"]), populate_existing=True
    )
    assert row is not None
    assert row.publication_state == "prepared"


def test_participation_revoke_replay_and_old_generation_never_revives(
    client, db, access_fixture
):
    """参加資格全体の停止と再送を検証し、期限延長・復活を拒否する。"""
    ctx = access_fixture
    publish(client, ctx)
    part = ctx["guests"][0][1]
    payload = dict(request_id=str(uuid4()), version=1, reason="参加期間終了")
    url = f"/projects/{ctx['project'].id}/guest-participations/{part.id}/revoke"
    response = client.post(url, json=payload)
    assert response.status_code == 200, response.text
    assert client.post(url, json=payload).json() == response.json()
    with pytest.raises(DBAPIError):
        db.execute(
            update(ProjectGuestParticipation)
            .where(ProjectGuestParticipation.id == part.id)
            .values(status="active", version=3)
        )
        db.commit()
    db.rollback()
    authorize_as(client, ctx["guests"][0][0])
    denied(client, ctx)
