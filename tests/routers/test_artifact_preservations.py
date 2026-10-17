"""添付の実体保全・障害回収・認可を、実Postgresと注入Storageで検証する。"""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError

from app.db.session import session_local
from app.main import create_app
from app.models.artifact_preservation import (
    ArtifactPreservation,
    ArtifactPreservationObject,
)
from app.models.document import DocumentAttachment, ProjectDocument
from app.models.rbac import Permission, Role, RolePermission
from app.routers.artifact_preservations import service
from app.services.audit_log import AuditLogService
from app.services.storage import StorageService
from app.services.trash import TrashService
from tests.fakes.storage import MemoryS3Client
from tests.helpers.auth import authorize_as


class PreservationS3(MemoryS3Client):
    """特定境界の中断・破損・回収失敗を注入する。"""

    def __init__(self):
        """元データと保全先を分ける。"""
        super().__init__()
        self.on_put = None
        self.fail_delete = False
        self.corrupt = False
        self.ttl = None

    def put_object(self, **kwargs):
        """PUT後の中断と読み戻し破損を模擬する。"""
        result = super().put_object(**kwargs)
        if self.corrupt:
            body, mime = self.objects[kwargs["Key"]]
            self.objects[kwargs["Key"]] = (b"!" * len(body), mime)
        if self.on_put:
            self.on_put(kwargs["Key"])
        return result

    def delete_object(self, **kwargs):
        """回収失敗の再試行を確認する。"""
        if self.fail_delete:
            raise RuntimeError("injected delete failure")
        return super().delete_object(**kwargs)

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn):
        """実際に指定した有効期間を記録する。"""
        self.ttl = ExpiresIn
        return super().generate_presigned_url(ClientMethod, Params, ExpiresIn)


@pytest.fixture
def context(
    client,
    db,
    create_test_user,
    create_test_project,
    assign_project_role,
    create_test_requirement_document,
    monkeypatch,
):
    """内部管理者・固定本文・元資料添付とダミーStorageを用意する。"""
    user = create_test_user()
    project = create_test_project()
    assign_project_role(user=user, project=project, role_key="project_admin")
    authorize_as(client, user)
    document = create_test_requirement_document(project=project)
    base = f"/projects/{project.id}/artifact-releases"
    request = {
        "source": {
            "kind": "requirement_document",
            "id": document.id,
            "version": document.version,
        }
    }
    preview = client.post(base + "/preview", json=request)
    assert preview.status_code == 200, preview.text
    release = client.post(
        base,
        json={
            **request,
            "request_id": str(uuid4()),
            "expected_scope_digest": preview.json()["scope_digest"],
        },
    ).json()
    source_document = ProjectDocument(
        project_id=project.id,
        title="元の設計資料",
        body="元の資料本文",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(source_document)
    db.flush()
    body = "添付の固定内容です。\n".encode()
    source = DocumentAttachment(
        id=uuid4(),
        document_id=source_document.id,
        filename="設計資料.txt",
        content_type="text/plain",
        byte_size=len(body),
        storage_key=f"projects/{project.id}/fixture/{uuid4()}",
        uploaded_by=user.id,
    )
    db.add(source)
    db.commit()
    fake = PreservationS3()
    fake.objects[source.storage_key] = (body, "text/plain")
    storage = StorageService(s3_client=fake)
    monkeypatch.setattr(service, "storage", storage)
    return dict(
        user=user,
        project=project,
        release=release,
        document=source_document,
        source=source,
        body=body,
        fake=fake,
        storage=storage,
        base=f"{base}/{release['id']}/attachment-preservations",
    )


def prepare(client, ctx, sources=None):
    """previewの確認値を保持して予約する。"""
    data = {
        "attachments": sources
        or [{"document_id": ctx["document"].id, "attachment_id": str(ctx["source"].id)}]
    }
    response = client.post(ctx["base"] + "/preview", json=data)
    assert response.status_code == 200, response.text
    request = {
        **data,
        "expected_plan_digest": response.json()["plan_digest"],
        "request_id": str(uuid4()),
    }
    reserved = client.post(ctx["base"], json=request)
    assert reserved.status_code == 201, reserved.text
    return reserved.json(), request


def advance(client, ctx, row):
    """明示した保全処理を1ステップ進める。"""
    return client.post(f"{ctx['base']}/{row['id']}/advance")


def test_reserve_copy_download_replay_and_no_source_change(client, db, context):
    """選択と内容hashを区別し、元を変えずに短期DLできる。"""
    ctx = context
    row, request = prepare(client, ctx)
    assert row["status"] == "preparing" and row["manifest_digest"] is None
    assert (
        client.get(
            f"{ctx['base']}/{row['id']}/files/{row['files'][0]['file_id']}/download"
        ).status_code
        == 409
    )

    def reserved_before_put(key):
        with session_local() as check:
            assert (
                check.scalar(
                    select(ArtifactPreservationObject.id).where(
                        ArtifactPreservationObject.storage_key == key
                    )
                )
                is not None
            )

    ctx["fake"].on_put = reserved_before_put
    result = advance(client, ctx, row)
    assert result.status_code == 200, result.text
    ready = result.json()
    assert ready["status"] == "ready" and ready["copied_count"] == 1
    assert (
        ready["files"][0]["content_digest"] == hashlib.sha256(ctx["body"]).hexdigest()
    )
    assert (
        "source_key" not in result.text
        and "storage_key" not in result.text
        and '"lease_id"' not in result.text
    )
    download = client.get(
        f"{ctx['base']}/{row['id']}/files/{row['files'][0]['file_id']}/download"
    )
    assert (
        download.status_code == 200 and download.headers["cache-control"] == "no-store"
    )
    assert ctx["fake"].ttl <= 60 and download.json()["expires_in"] == 60
    assert "attachment;" in ctx["fake"].presigned_params["ResponseContentDisposition"]
    assert client.post(ctx["base"], json=request).json()["id"] == row["id"]
    assert (
        advance(client, ctx, row).json()["manifest_digest"] == ready["manifest_digest"]
    )
    assert client.get(ctx["base"]).json()["total"] == 1
    db.refresh(ctx["source"])
    db.refresh(ctx["document"])
    assert ctx["source"].deleted_at is None and ctx["document"].body == "元の資料本文"
    assert ctx["fake"].objects[ctx["source"].storage_key][0] == ctx["body"]


def test_snapshot_bytes_survive_original_purge(client, db, context):
    """実際の旧TrashServiceが元キーを削除しても保全実体を保持する。"""
    ctx = context
    row, _ = prepare(client, ctx)
    ready = advance(client, ctx, row).json()
    original_key = ctx["source"].storage_key
    ctx["document"].deleted_at = datetime.now(UTC) - timedelta(days=31)
    db.commit()
    assert TrashService(ctx["storage"]).purge_one(
        db,
        {
            "project_id": ctx["project"].id,
            "kind": "document",
            "id": str(ctx["document"].id),
        },
        datetime.now(UTC),
    )
    assert original_key not in ctx["fake"].objects
    obj = db.scalar(
        select(ArtifactPreservationObject).where(ArtifactPreservationObject.selected)
    )
    assert ctx["fake"].objects[obj.storage_key][0] == ctx["body"]
    assert (
        client.get(f"{ctx['base']}/{row['id']}").json()["manifest_digest"]
        == ready["manifest_digest"]
    )


def test_plan_change_and_request_conflict(client, db, context):
    """同じ操作IDの別入力と、確認後の元属性変更を区別する。"""
    ctx = context
    row, data = prepare(client, ctx)
    assert (
        client.post(
            ctx["base"], json={**data, "expected_plan_digest": "0" * 64}
        ).status_code
        == 409
    )
    ctx["source"].filename = "別の名前.txt"
    db.commit()
    assert (
        client.post(ctx["base"], json={**data, "request_id": str(uuid4())}).status_code
        == 409
    )


@pytest.mark.parametrize("mode", ["after_put", "corrupt", "missing"])
def test_copy_failure_never_becomes_ready_and_can_retry(client, db, context, mode):
    """PUT後中断・読み戻し破損・元欠損で部分完成を出さない。"""
    ctx = context
    row, _ = prepare(client, ctx)
    if mode == "after_put":

        def fail(key):
            raise RuntimeError("injected after PUT")

        ctx["fake"].on_put = fail
    elif mode == "corrupt":
        ctx["fake"].corrupt = True
    else:
        ctx["fake"].objects.pop(ctx["source"].storage_key)
    result = advance(client, ctx, row)
    assert result.status_code in (200, 400), result.text
    current = client.get(f"{ctx['base']}/{row['id']}").json()
    assert current["status"] == "failed" and current["copied_count"] == 0
    assert all(
        not key.startswith("artifact-preservations/") for key in ctx["fake"].objects
    )
    ctx["fake"].on_put = None
    ctx["fake"].corrupt = False
    ctx["fake"].objects[ctx["source"].storage_key] = (ctx["body"], "text/plain")
    assert advance(client, ctx, row).json()["status"] == "ready"


def test_delete_failure_and_late_put_remain_recoverable(client, db, context):
    """一度の削除成功で台帳を消さず、遅延PUTも再回収する。"""
    ctx = context
    row, _ = prepare(client, ctx)

    def fail(key):
        raise RuntimeError("interrupted")

    ctx["fake"].on_put = fail
    ctx["fake"].fail_delete = True
    assert advance(client, ctx, row).json()["cleanup_pending"] == 1
    obj = db.scalar(select(ArtifactPreservationObject))
    key = obj.storage_key
    ctx["fake"].on_put = None
    ctx["fake"].fail_delete = False
    url = f"{ctx['base']}/{row['id']}/cleanup"
    assert client.post(url, json={}).json()["cleanup_pending"] == 0
    assert key not in ctx["fake"].objects
    ctx["fake"].objects[key] = (ctx["body"], "text/plain")
    assert client.post(url, json={}).status_code == 200
    assert (
        key not in ctx["fake"].objects
        and db.get(ArtifactPreservationObject, obj.id) is not None
    )


def test_expired_lease_never_publishes_and_new_attempt_is_separate(client, db, context):
    """古い結果を採用せず、再試行で別のキーを予約する。"""
    ctx = context
    row, _ = prepare(client, ctx)

    def expire(key):
        with session_local() as change:
            job = change.get(ArtifactPreservation, UUID(row["id"]))
            assert job is not None
            job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            change.commit()

    ctx["fake"].on_put = expire
    assert advance(client, ctx, row).status_code == 409
    ctx["fake"].on_put = None
    assert advance(client, ctx, row).json()["status"] == "ready"
    objects = list(db.scalars(select(ArtifactPreservationObject)))
    assert len(objects) == 2 and len({o.storage_key for o in objects}) == 2
    assert sum(o.selected for o in objects) == 1


def test_audit_failure_rolls_back_completion(client, db, context, monkeypatch):
    """完成記録だけが失敗した場合も、採用実体を残した成功にしない。"""
    row, _ = prepare(client, context)
    old = AuditLogService.record

    def record(self, db, **kwargs):
        if kwargs["event_type"] == "artifact_preservation.completed":
            raise RuntimeError("audit failed")
        return old(self, db, **kwargs)

    monkeypatch.setattr(AuditLogService, "record", record)
    result = advance(client, context, row)
    assert result.status_code == 200 and result.json()["status"] == "failed"
    assert result.json()["copied_count"] == 0


def test_ready_manifest_and_selected_object_are_sealed(client, db, context):
    """完成版への直接DMLと取消による実体削除を拒否する。"""
    ctx = context
    row, _ = prepare(client, ctx)
    assert advance(client, ctx, row).json()["status"] == "ready"
    for statement in [
        update(ArtifactPreservation).values(manifest_digest="0" * 64),
        update(ArtifactPreservationObject).values(storage_key="wrong"),
    ]:
        with pytest.raises(DBAPIError):
            db.execute(statement)
            db.commit()
        db.rollback()
    assert (
        client.post(
            f"{ctx['base']}/{row['id']}/cleanup", json={"cancel_preparation": True}
        ).status_code
        == 409
    )
    before = dict(ctx["fake"].objects)
    assert client.post(f"{ctx['base']}/{row['id']}/cleanup", json={}).status_code == 200
    assert ctx["fake"].objects == before


@pytest.mark.parametrize(
    "role,guest",
    [("member", False), ("viewer", False), (None, False), ("manager", True)],
)
def test_unprivileged_access_denied(
    client, db, context, create_test_user, assign_project_role, role, guest
):
    """読取・件数・再送・進行・DLに同じ現在権限を使う。"""
    ctx = context
    row, data = prepare(client, ctx)
    ready = advance(client, ctx, row).json()
    peer = create_test_user(email="reader@example.com")
    if role:
        assign_project_role(user=peer, project=ctx["project"], role_key=role)
    if guest:
        peer.user_type = "guest"
        db.commit()
    authorize_as(client, peer)
    for suffix in [
        "",
        f"/{row['id']}",
        f"/{row['id']}/files/{ready['files'][0]['file_id']}/download",
    ]:
        assert client.get(ctx["base"] + suffix).status_code == 403
    assert client.post(ctx["base"], json=data).status_code == 403
    assert advance(client, ctx, row).status_code == 403


def test_revoked_permission_during_copy_does_not_publish(client, db, context):
    """I/O前だけの判定で、失効後に完成させない。"""
    ctx = context
    row, _ = prepare(client, ctx)

    def revoke(key):
        with session_local() as change:
            role = change.scalar(
                select(Role.id).where(
                    Role.key == "project_admin", Role.scope == "project"
                )
            )
            permission = change.scalar(
                select(Permission.id).where(Permission.code == "document:read")
            )
            change.execute(
                delete(RolePermission).where(
                    RolePermission.role_id == role,
                    RolePermission.permission_id == permission,
                )
            )
            change.commit()

    ctx["fake"].on_put = revoke
    assert advance(client, ctx, row).status_code == 403
    db.expire_all()
    assert db.get(ArtifactPreservation, UUID(row["id"])).status == "failed"
    assert not any(
        key.startswith("artifact-preservations/") for key in ctx["fake"].objects
    )


def test_cancel_fences_late_result(client, db, context):
    """取消後に返る結果を採用せず、そのキーを回収できる。"""
    ctx = context
    row, _ = prepare(client, ctx)

    def cancel(key):
        with session_local() as change:
            job = change.get(ArtifactPreservation, UUID(row["id"]))
            assert job is not None
            job.status = "cancelled"
            job.lease_id = None
            job.lease_expires_at = None
            change.commit()

    ctx["fake"].on_put = cancel
    assert advance(client, ctx, row).status_code == 409
    assert client.get(f"{ctx['base']}/{row['id']}").json()["status"] == "cancelled"


def test_busy_lease_and_input_validation(client, db, context):
    """同時処理を待たず拒否し、任意保存先や重複入力を受け入れない。"""
    ctx = context
    row, data = prepare(client, ctx)
    job = db.get(ArtifactPreservation, UUID(row["id"]))
    job.lease_id = uuid4()
    job.lease_expires_at = datetime.now(UTC) + timedelta(seconds=60)
    db.commit()
    assert advance(client, ctx, row).status_code == 409
    assert (
        client.post(ctx["base"], json={**data, "storage_key": "arbitrary"}).status_code
        == 422
    )
    assert (
        client.post(
            ctx["base"] + "/preview", json={"attachments": data["attachments"] * 2}
        ).status_code
        == 422
    )


def test_multiple_files_are_not_downloadable_until_all_complete(client, db, context):
    """別ファイルの完了を待つ間に部分版のDLを許可しない。"""
    ctx = context
    second = DocumentAttachment(
        id=uuid4(),
        document_id=ctx["document"].id,
        filename="追加.txt",
        content_type="text/plain",
        byte_size=3,
        storage_key=f"fixture/{uuid4()}",
        uploaded_by=ctx["user"].id,
    )
    db.add(second)
    db.commit()
    ctx["fake"].objects[second.storage_key] = (b"two", "text/plain")
    sources = [
        {"document_id": ctx["document"].id, "attachment_id": str(item.id)}
        for item in [ctx["source"], second]
    ]
    row, _ = prepare(client, ctx, sources)
    first = advance(client, ctx, row).json()
    assert first["status"] == "preparing" and first["copied_count"] == 1
    assert (
        client.get(
            f"{ctx['base']}/{row['id']}/files/{first['files'][0]['file_id']}/download"
        ).status_code
        == 409
    )
    final = advance(client, ctx, row).json()
    assert final["status"] == "ready" and final["copied_count"] == 2
    assert [item["attachment_id"] for item in final["files"]] == [
        item["attachment_id"] for item in sources
    ]


def test_other_project_source_and_parent_cannot_mix(
    client, db, context, create_test_project, assign_project_role
):
    """両案件の権限があっても、別案件の固定本文や添付を混ぜない。"""
    ctx = context
    other = create_test_project()
    assign_project_role(user=ctx["user"], project=other, role_key="project_admin")
    document = ProjectDocument(
        project_id=other.id,
        title="別案件",
        created_by=ctx["user"].id,
        updated_by=ctx["user"].id,
    )
    db.add(document)
    db.flush()
    attachment = DocumentAttachment(
        id=uuid4(),
        document_id=document.id,
        filename="other.txt",
        content_type="text/plain",
        byte_size=1,
        storage_key=f"other/{uuid4()}",
        uploaded_by=ctx["user"].id,
    )
    db.add(attachment)
    db.commit()
    response = client.post(
        ctx["base"] + "/preview",
        json={
            "attachments": [
                {"document_id": document.id, "attachment_id": str(attachment.id)}
            ]
        },
    )
    assert response.status_code == 404 and "other.txt" not in response.text
    foreign = ctx["base"].replace(
        f"/projects/{ctx['project'].id}/", f"/projects/{other.id}/"
    )
    assert client.get(foreign).status_code == 404


def test_concurrent_advance_uses_one_lease(client, db, context):
    """I/O中の二重実行を拒否し、採用実体を一つにする。"""
    ctx = context
    row, _ = prepare(client, ctx)
    started, release = Event(), Event()

    def pause(key):
        started.set()
        assert release.wait(8)

    ctx["fake"].on_put = pause

    def worker():
        with TestClient(create_app()) as peer:
            authorize_as(peer, ctx["user"])
            return advance(peer, ctx, row)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(worker)
        assert started.wait(8)
        try:
            assert advance(client, ctx, row).status_code == 409
        finally:
            release.set()
        result = pending.result()
    assert result.status_code == 200 and result.json()["status"] == "ready"
    assert len(list(db.scalars(select(ArtifactPreservationObject)))) == 1


def test_maximum_attachment_bytes_are_preserved(client, db, context):
    """既存20MiB上限のファイルを切り捨てずに保全する。"""
    ctx = context
    content = b"x" * (20 * 1024 * 1024)
    ctx["source"].byte_size = len(content)
    db.commit()
    ctx["fake"].objects[ctx["source"].storage_key] = (content, "text/plain")
    row, _ = prepare(client, ctx)
    result = advance(client, ctx, row)
    assert result.status_code == 200 and result.json()["status"] == "ready"
    assert (
        result.json()["files"][0]["content_digest"]
        == hashlib.sha256(content).hexdigest()
    )
