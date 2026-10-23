"""公開準備の原子性・不変性・現在の認可と本文の完全性を確認する。"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.models.artifact_publication import ArtifactPublication, ArtifactPublicationNode
from app.models.audit_log import AuditLog
from app.models.rbac import Permission, Role, RolePermission
from app.models.session import UserSession
from app.models.user import User
from app.routers.artifact_publications import service
from app.services.artifact_release_snapshot import digest
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_preservations import advance
from tests.routers.test_artifact_preservations import context as preservation_context  # noqa: F401
from tests.routers.test_artifact_preservations import prepare as prepare_preservation
from tests.routers.test_artifact_releases import context as context  # noqa: F401
from tests.routers.test_artifact_releases import save
from tests.routers.test_artifact_test_design import freeze
from tests.routers.test_test_designs import design_context as design_context  # noqa: F401
from tests.routers.test_test_designs import graph


def publication_base(ctx):
    """Projectに限定した新APIの接頭辞。"""
    return f"/projects/{ctx['project'].id}/artifact-publications"


def prepare(client, base, release, preservation_id=None):
    """確認済みの両digestを作成へ引き継ぐ。"""
    data = dict(
        release_id=release["id"],
        preservation_id=preservation_id,
        request_id=str(uuid4()),
    )
    response = client.post(base + "/preview", json=data)
    assert response.status_code == 200, response.text
    return dict(
        **data,
        expected_public_digest=response.json()["public_digest"],
        expected_source_manifest_digest=response.json()["source_manifest_digest"],
    )


def create(client, ctx, release=None):
    """固定版から未公開の準備版を作る。"""
    if release is None:
        release, _ = save(client, ctx)
    base = publication_base(ctx)
    data = prepare(client, base, release)
    response = client.post(base, json=data)
    assert response.status_code == 201, response.text
    return response.json(), data


def nodes(client, base, identity, size=1):
    """全ページを取得し、ordinalと個別hashを検証する。"""
    result, cursor = [], None
    for _ in range(1000):
        response = client.get(
            f"{base}/{identity}/nodes",
            params={"page_size": size, **({"cursor": cursor} if cursor else {})},
        )
        assert response.status_code == 200, response.text
        assert len(response.content) <= 1024 * 1024
        page = response.json()
        result.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            assert len(result) == page["total"]
            assert [n["ordinal"] for n in result] == list(range(len(result)))
            for n in result:
                assert n["content_digest"] == digest(
                    {k: v for k, v in n.items() if k != "content_digest"}
                )
            return result
    raise AssertionError("cursor did not terminate")


def test_publication_whole_content_digest_replay_and_current_source_change(
    client, db, context
):
    """内部metadataを除き、本文・構成・固定時状態を保持し再送を重複保存しない。"""
    release, _ = save(client, context)
    base = publication_base(context)
    data = prepare(client, base, release)
    assert db.scalar(select(func.count()).select_from(ArtifactPublication)) == 0
    response = client.post(base, json=data)
    assert response.status_code == 201, response.text
    row = response.json()
    identity = row["content"]["id"]
    assert row["state"] == "prepared" and row["content"]["files"] == []
    assert "project_id" not in row["content"] and "release_id" not in row["content"]
    all_nodes = nodes(client, base, identity)
    assert {n["node_kind"] for n in all_nodes} == {
        "requirement_document",
        "section",
        "requirement",
        "requirement_detail",
        "specification",
    }
    assert not any("source_id" in n or "created_by" in n["payload"] for n in all_nodes)
    assert next(
        n["payload"] for n in all_nodes if n["node_kind"] == "requirement_detail"
    )["detail_json"] == {"rule": "保存後に復帰"}
    ordinal_map = {n["id"]: n["ordinal"] for n in all_nodes}
    canonical = [
        dict(
            **{
                k: v
                for k, v in n.items()
                if k not in {"id", "parent_id", "content_digest"}
            },
            parent_ordinal=ordinal_map[n["parent_id"]] if n["parent_id"] else None,
        )
        for n in all_nodes
    ]
    assert (
        digest(
            dict(
                schema_version=1,
                projection_version="public-preview-v1",
                title=row["content"]["title"],
                nodes=canonical,
                files=[],
            )
        )
        == row["content"]["public_digest"]
    )
    assert client.post(base, json=data).status_code == 200
    assert (
        client.post(base, json={**data, "expected_public_digest": "0" * 64}).status_code
        == 409
    )
    second_data = prepare(client, base, release)
    second = client.post(base, json=second_data).json()
    assert second["content"]["public_digest"] == row["content"]["public_digest"]
    assert second["content"]["id"] != identity
    context["req"].description = "最新版へ変更"
    db.commit()
    assert nodes(client, base, identity) == all_nodes
    assert client.get(base).json()["total"] == 2
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.event_type == "artifact_publication.prepared")
        )
        == 2
    )


@pytest.mark.parametrize(
    "code",
    [
        "artifact_publication:manage",
        "artifact_publication:read",
        "artifact_release:create",
        "artifact_release:read",
        "requirement:read",
        "document:read",
    ],
)
def test_permission_revocation_filters_read_and_count(client, db, context, code):
    """新旧権限と同梱sourceを現在値で確認し、COUNTからも除く。"""
    row, data = create(client, context)
    base = publication_base(context)
    role_id = db.scalar(
        select(Role.id).where(Role.key == "project_admin", Role.scope == "project")
    )
    permission_id = db.scalar(select(Permission.id).where(Permission.code == code))
    assert permission_id is not None
    db.execute(
        delete(RolePermission).where(
            RolePermission.role_id == role_id,
            RolePermission.permission_id == permission_id,
        )
    )
    db.commit()
    assert (
        client.post(base, json={**data, "request_id": str(uuid4())}).status_code == 403
    )
    response = client.get(f"{base}/{row['content']['id']}")
    if code in {"artifact_publication:manage", "artifact_release:create"}:
        assert response.status_code == 200
    else:
        assert response.status_code == 403
        listing = client.get(base)
        assert listing.status_code == 403 or listing.json()["total"] == 0


@pytest.mark.parametrize(
    "role,guest", [("member", False), ("viewer", False), ("project_admin", True)]
)
def test_no_guest_or_low_role_access(
    client, db, context, create_test_user, assign_project_role, role, guest
):
    """表示制御に依存せず、guestへ内部機能を渡さない。"""
    row, data = create(client, context)
    peer = create_test_user(email="publication-peer@example.com")
    assign_project_role(user=peer, project=context["project"], role_key=role)
    if guest:
        peer.user_type = "guest"
        db.commit()
    authorize_as(client, peer)
    base = publication_base(context)
    assert (
        client.post(
            base + "/preview",
            json={k: v for k, v in data.items() if not k.startswith("expected_")},
        ).status_code
        == 403
    )
    assert client.get(f"{base}/{row['content']['id']}/nodes").status_code == 403


def test_other_project_and_foreign_cursor_actor(
    client, db, context, create_test_project, assign_project_role, create_test_user
):
    """他Project/別版/別本人のcursorを再利用できない。"""
    row, data = create(client, context)
    base = publication_base(context)
    identity = row["content"]["id"]
    cursor = client.get(f"{base}/{identity}/nodes", params={"page_size": 1}).json()[
        "next_cursor"
    ]
    assert cursor
    other = create_test_project()
    assign_project_role(user=context["user"], project=other, role_key="project_admin")
    assert (
        client.get(f"/projects/{other.id}/artifact-publications/{identity}").status_code
        == 404
    )
    assert (
        client.post(
            f"/projects/{other.id}/artifact-publications/preview",
            json={k: v for k, v in data.items() if not k.startswith("expected_")},
        ).status_code
        == 404
    )
    second, _ = create(client, context)
    assert (
        client.get(
            f"{base}/{second['content']['id']}/nodes", params={"cursor": cursor}
        ).status_code
        == 409
    )
    peer = create_test_user(email="publication-peer@example.com")
    assign_project_role(user=peer, project=context["project"], role_key="project_admin")
    authorize_as(client, peer)
    assert (
        client.get(f"{base}/{identity}/nodes", params={"cursor": cursor}).status_code
        == 409
    )
    assert (
        client.get(
            f"{base}/{identity}/nodes", params={"cursor": cursor + "x"}
        ).status_code
        == 409
    )


@pytest.mark.parametrize("state", ["csrf", "session", "mfa", "inactive"])
def test_authentication_and_csrf(client, db, context, monkeypatch, state):
    """認証と現在session/MFA/CSRFを全て維持する。"""
    release, _ = save(client, context)
    base = publication_base(context)
    data = prepare(client, base, release)
    sid = authorize_as(client, context["user"])
    if state == "csrf":
        client.headers.pop(settings.csrf_header_name)
    elif state == "session":
        db.get(UserSession, sid).revoked_at = datetime.now(UTC)
        db.commit()
    elif state == "mfa":
        monkeypatch.setattr(settings, "mfa_required", True)
    else:
        db.get(User, context["user"].id).is_active = False
        db.commit()
    response = client.post(base, json=data)
    assert response.status_code in {401, 403}
    assert response.headers["cache-control"] == "private, no-store"
    assert db.scalar(select(func.count()).select_from(ArtifactPublication)) == 0


def test_seal_incomplete_rejection_rollback_and_preserving_downgrade(
    client, db, context, monkeypatch
):
    """途中失敗をrollbackし、封印した準備版と全nodeをDBでも不変にする。"""
    release, _ = save(client, context)
    base = publication_base(context)
    data = prepare(client, base, release)
    original = service.repository.create

    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected after seal")

    with monkeypatch.context() as patch:
        patch.setattr(service.repository, "create", failed)
        with pytest.raises(RuntimeError, match="injected after seal"):
            client.post(base, json=data)
    assert db.scalar(select(func.count()).select_from(ArtifactPublication)) == 0
    row = client.post(base, json=data).json()
    identity = UUID(row["content"]["id"])
    for statement in [
        update(ArtifactPublication)
        .where(ArtifactPublication.id == identity)
        .values(title="変更"),
        delete(ArtifactPublication).where(ArtifactPublication.id == identity),
        update(ArtifactPublicationNode)
        .where(ArtifactPublicationNode.publication_id == identity)
        .values(payload={}),
        delete(ArtifactPublicationNode).where(
            ArtifactPublicationNode.publication_id == identity
        ),
    ]:
        with pytest.raises(DBAPIError):
            db.execute(statement)
            db.commit()
        db.rollback()
    assert client.get(f"{base}/{identity}").status_code == 200
    from alembic.config import Config

    from alembic import command

    with pytest.raises(RuntimeError, match="公開準備版"):
        command.downgrade(Config("alembic.ini"), "c9e3f6a1d547")


def test_test_graph_public_projection_whole_and_multi_parts(client, db, design_context):
    """テスト本文の値/UUID/書式を保持し、内部envelopeだけを除く。"""
    _, url, design, user, project = design_context
    data = graph(design)
    data["items"][0]["test_data"] = "秘密は事前確認するダミー"
    data["items"][0]["content"] = 'あ"\\' * 6000
    data["items"].extend(
        {**data["items"][0], "id": str(uuid4()), "code": f"T{i + 10:03d}"}
        for i in range(20)
    )
    saved = client.put(url, json=data)
    assert saved.status_code == 200, saved.text
    source = saved.json()
    release, _ = freeze(client, f"/projects/{project.id}/artifact-releases", source)
    ctx = {"project": project}
    row, _ = create(client, ctx, release)
    base = publication_base(ctx)
    all_nodes = nodes(client, base, row["content"]["id"], size=1)
    assert len(all_nodes) > 2
    raw = "".join(
        n["payload"]["text"] for n in all_nodes if n["node_kind"] == "test_design_part"
    )
    expected = {
        k: v for k, v in source.items() if k not in {"id", "project_id", "updated_at"}
    }
    assert json.loads(raw) == expected
    assert (
        hashlib.sha256(raw.encode()).hexdigest()
        == all_nodes[0]["payload"]["graph_digest"]
    )
    assert '"project_id"' not in raw


def test_stop_write_keeps_reader_and_download_is_explicit(client, context, monkeypatch):
    """準備版生成の停止でreaderを壊さず、新DLは専用POSTだけに限定する。"""
    row, data = create(client, context)
    base = publication_base(context)
    monkeypatch.setattr(
        "app.services.artifact_publication.PUBLICATION_WRITES_ENABLED", False
    )
    assert client.post(base, json=data).status_code == 403
    assert client.get(f"{base}/{row['content']['id']}").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    downloads = {
        p: set(operations)
        for p, operations in paths.items()
        if p.startswith("/shared") and "download" in p
    }
    assert downloads == {
        "/shared/publications/{publication_id}/files/{file_id}/download": {"post"}
    }
    assert len([p for p in paths if "artifact-publications" in p]) == 10


def test_capacity_conflict_and_source_purge(client, db, context, monkeypatch):
    """容量超過と異なる確認digestを拒否し、元purge後も本文を保持する。"""
    from app.repositories.trash import TrashRepository

    release, _ = save(client, context)
    base = publication_base(context)
    data = prepare(client, base, release)
    assert (
        client.post(base, json={**data, "expected_public_digest": "0" * 64}).status_code
        == 409
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            "app.services.artifact_publication_projection.MAX_SNAPSHOT_BYTES", 1
        )
        assert client.post(base, json=data).status_code == 413
    row = client.post(base, json=data).json()
    before = nodes(client, base, row["content"]["id"])
    repository = TrashRepository()
    targets = repository.purge_plan(db, "requirement_documents", context["doc"].id)
    assert "artifact_publications" not in targets
    repository.purge(db, targets, repository.detachments(db, targets))
    db.commit()
    assert nodes(client, base, row["content"]["id"]) == before


def test_concurrent_replay_is_one_publication(client, db, context, monkeypatch):
    """両方が未作成を読んだ並行処理でも1件へ収束する。"""
    from threading import Barrier

    release, _ = save(client, context)
    base = publication_base(context)
    data = prepare(client, base, release)
    barrier = Barrier(2)
    original = service.sources

    def simultaneous(*args, **kwargs):
        result = original(*args, **kwargs)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(service, "sources", simultaneous)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(base, json=data), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 201]
    assert len({r.json()["content"]["id"] for r in responses}) == 1
    assert db.scalar(select(func.count()).select_from(ArtifactPublication)) == 1


def test_unsealed_incomplete_header_cannot_commit(client, db, context):
    """アプリの封印処理を経由しない不完結な保存もDBで拒否する。"""
    row, _ = create(client, context)
    saved = db.get(ArtifactPublication, UUID(row["content"]["id"]))
    values = {
        c.name: getattr(saved, c.name) for c in ArtifactPublication.__table__.columns
    }
    values.update(id=uuid4(), request_id=uuid4(), sealed=False)
    with pytest.raises(DBAPIError):
        db.add(ArtifactPublication(**values))
        db.commit()
    db.rollback()
    assert db.scalar(select(func.count()).select_from(ArtifactPublication)) == 1


def test_attachment_manifest_projection_and_not_ready_denial(client, db, request):
    """実体を触らずready構成の全ファイルを公開一覧へ写す。"""
    ctx = request.getfixturevalue("preservation_context")
    base = publication_base(ctx)
    preservation, _ = prepare_preservation(client, ctx)
    body = dict(
        release_id=ctx["release"]["id"],
        preservation_id=preservation["id"],
        request_id=str(uuid4()),
    )
    assert client.post(base + "/preview", json=body).status_code == 409
    ready = advance(client, ctx, preservation)
    assert ready.status_code == 200, ready.text
    object_bytes = dict(ctx["fake"].objects)
    request = prepare(client, base, ctx["release"], preservation["id"])
    saved = client.post(base, json=request)
    assert saved.status_code == 201, saved.text
    files = saved.json()["content"]["files"]
    assert len(files) == 1 and files[0]["filename"] == "設計資料.txt"
    assert files[0]["content_digest"] == hashlib.sha256(ctx["body"]).hexdigest()
    assert set(files[0]) == {
        "id",
        "filename",
        "content_type",
        "byte_size",
        "content_digest",
    }
    assert dict(ctx["fake"].objects) == object_bytes
    assert saved.headers["cache-control"] == "private, no-store"
