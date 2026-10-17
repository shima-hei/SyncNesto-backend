"""固定版本文の保全・認可・競合・分割取得を実PostgreSQLで確認する。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.artifact_release import ArtifactRelease, ArtifactReleaseNode
from app.models.audit_log import AuditLog
from app.models.rbac import Permission, Role, RolePermission
from app.models.requirement import Requirement, RequirementDetail
from app.models.specification import Specification
from app.routers.artifact_releases import service
from app.services.artifact_release_snapshot import MAX_PAGE_BYTES
from tests.helpers.auth import authorize_as


@pytest.fixture
def context(
    client,
    db,
    create_test_user,
    create_test_project,
    assign_project_role,
    create_test_requirement_document,
    create_test_requirement_section,
):
    """元の要件・章・設計と内部管理者を用意する。"""
    user = create_test_user()
    project = create_test_project()
    assign_project_role(user=user, project=project, role_key="project_admin")
    doc = create_test_requirement_document(project=project)
    section = create_test_requirement_section(document=doc, content="固定する章本文")
    req = Requirement(
        document_id=doc.id,
        section_id=section.id,
        requirement_code="REQ-001",
        requirement_type="functional",
        title="保存を続けられる",
        description="要件本文",
        acceptance_criteria="戻っても入力が残る",
        owner_id=user.id,
    )
    spec = Specification(
        project_id=project.id,
        kind="screen",
        title="入力画面",
        summary="入力の保護",
        content={"type": "screen", "route": "/edit", "fields": []},
        created_by=user.id,
        updated_by=user.id,
    )
    db.add_all([req, spec])
    db.flush()
    db.add(
        RequirementDetail(
            requirement_id=req.id,
            detail_type="functional",
            detail_json={"rule": "保存後に復帰"},
        )
    )
    db.commit()
    authorize_as(client, user)
    return dict(
        user=user,
        project=project,
        doc=doc,
        section=section,
        req=req,
        spec=spec,
        base=f"/projects/{project.id}/artifact-releases",
    )


def preview_data(ctx, *, kind="requirement_document", include=True):
    """架空の対象IDと版でpreview入力を作る。"""
    source = ctx[
        {"requirement_document": "doc", "requirement": "req", "specification": "spec"}[
            kind
        ]
    ]
    return {
        "source": {"kind": kind, "id": source.id, "version": source.version},
        "included_sources": (
            [
                {
                    "kind": "specification",
                    "id": ctx["spec"].id,
                    "version": ctx["spec"].version,
                }
            ]
            if include and kind != "specification"
            else []
        ),
    }


def prepare(client, ctx, data=None):
    """previewを実行し、その確認結果を保存requestへ渡す。"""
    data = data or preview_data(ctx)
    response = client.post(ctx["base"] + "/preview", json=data)
    assert response.status_code == 200, response.text
    return {
        **data,
        "request_id": str(uuid4()),
        "expected_scope_digest": response.json()["scope_digest"],
    }


def save(client, ctx, data=None):
    """新しい固定版を作成する。"""
    data = prepare(client, ctx, data)
    response = client.post(ctx["base"], json=data)
    assert response.status_code == 201, response.text
    return response.json(), data


def all_nodes(client, ctx, release_id, *, size=100):
    """署名cursorをたどり、byte上限と通番を確認する。"""
    result, cursor = [], None
    for _ in range(1000):
        params = {"page_size": size}
        if cursor:
            params["cursor"] = cursor
        response = client.get(f"{ctx['base']}/{release_id}/nodes", params=params)
        assert response.status_code == 200, response.text
        assert len(response.content) <= MAX_PAGE_BYTES
        data = response.json()
        result.extend(data["items"])
        cursor = data["next_cursor"]
        if cursor is None:
            assert len(result) == data["total"]
            assert [n["ordinal"] for n in result] == list(range(len(result)))
            return result
    raise AssertionError("cursor did not terminate")


def test_snapshot_preview_create_trace_and_replay(client, db, context):
    """既存本文・旧承認を変えず、型別詳細と項目IDを保全する。"""
    data = prepare(client, context)
    assert db.scalar(select(func.count()).select_from(ArtifactRelease)) == 0
    created = client.post(context["base"], json=data)
    assert created.status_code == 201, created.text
    row = created.json()
    nodes = all_nodes(client, context, row["id"], size=2)
    assert {n["node_kind"] for n in nodes} == {
        "requirement_document",
        "section",
        "requirement",
        "requirement_detail",
        "specification",
    }
    assert all(
        "owner_id" not in n["payload"] and "created_by" not in n["payload"]
        for n in nodes
    )
    assert next(n for n in nodes if n["node_kind"] == "requirement_detail")["payload"][
        "detail_json"
    ] == {"rule": "保存後に復帰"}
    again = client.post(context["base"], json=data)
    assert again.status_code == 200 and again.json()["id"] == row["id"]
    altered = {**data, "expected_scope_digest": "0" * 64}
    assert client.post(context["base"], json=altered).status_code == 409
    assert db.scalar(select(func.count()).select_from(ArtifactRelease)) == 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.event_type == "artifact_release.created")
        )
        == 1
    )
    db.refresh(context["req"])
    assert context["req"].version == 1 and context["req"].status == "draft"


def test_child_and_detail_changes_invalidate_scope_without_parent_version(
    client, db, context
):
    """親の版だけでは検出できない子本文や詳細の変更も検出する。"""
    data = prepare(client, context)
    detail = db.scalar(select(RequirementDetail))
    detail.detail_json = {"rule": "変更した詳細"}
    db.commit()
    response = client.post(context["base"], json=data)
    assert response.status_code == 409
    assert set(response.json()["current"]) == {"scope_digest"}
    assert "変更した詳細" not in response.text
    assert db.scalar(select(func.count()).select_from(ArtifactRelease)) == 0
    data = prepare(client, context)
    context["req"].title = "preview後の更新"
    context["req"].version += 1
    db.commit()
    assert client.post(context["base"], json=data).status_code == 409


def test_snapshot_survives_source_purge(client, db, context):
    """元資源の既存purgeに固定版を巻き込まない。"""
    from app.repositories.trash import TrashRepository

    row, data = save(client, context)
    before = all_nodes(client, context, row["id"])
    repository = TrashRepository()
    targets = repository.purge_plan(db, "requirement_documents", context["doc"].id)
    assert not targets.get("artifact_releases")
    assert not targets.get("artifact_release_nodes")
    repository.purge(db, targets, repository.detachments(db, targets))
    db.commit()
    assert all_nodes(client, context, row["id"]) == before
    metadata = client.get(f"{context['base']}/{row['id']}").json()
    assert metadata["source_state"] == "deleted"
    assert metadata["current_source_version"] is None
    assert client.post(context["base"], json=data).json()["id"] == row["id"]


@pytest.mark.parametrize(
    "role,system,guest,expected",
    [
        ("project_admin", None, False, 200),
        ("manager", None, False, 200),
        ("member", None, False, 403),
        ("viewer", None, False, 403),
        (None, "system_admin", False, 403),
        (None, None, False, 403),
        ("manager", None, True, 403),
    ],
)
def test_permission_matrix(
    client,
    db,
    context,
    create_test_user,
    assign_project_role,
    role,
    system,
    guest,
    expected,
):
    """運営権限・guest・viewerを内部固定版の権限に変換しない。"""
    row, data = save(client, context)
    user = create_test_user(email="reader@example.com", system_role=system)
    if role:
        assign_project_role(user=user, project=context["project"], role_key=role)
    if guest:
        user.user_type = "guest"
        db.commit()
    authorize_as(client, user)
    for path in ["", f"/{row['id']}", f"/{row['id']}/nodes"]:
        assert client.get(context["base"] + path).status_code == expected
    assert (
        client.post(
            context["base"] + "/preview", json=preview_data(context)
        ).status_code
        == expected
    )
    response = client.post(context["base"], json={**data, "request_id": str(uuid4())})
    assert response.status_code == (201 if expected == 200 else expected)


def test_partial_source_permission_hides_list_count_and_replay(client, db, context):
    """含まれる設計が読めなくなったら件数にも本文にも出さない。"""
    combined, data = save(client, context)
    plain, _ = save(client, context, preview_data(context, include=False))
    role_id = db.scalar(
        select(Role.id).where(Role.key == "project_admin", Role.scope == "project")
    )
    permission_id = db.scalar(
        select(Permission.id).where(Permission.code == "document:read")
    )
    db.execute(
        delete(RolePermission).where(
            RolePermission.role_id == role_id,
            RolePermission.permission_id == permission_id,
        )
    )
    db.commit()
    response = client.get(context["base"])
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [r["id"] for r in response.json()["items"]] == [plain["id"]]
    assert client.get(f"{context['base']}/{combined['id']}").status_code == 403
    assert client.post(context["base"], json=data).status_code == 403


def test_other_project_and_source_rejected(
    client, context, create_test_project, assign_project_role
):
    """同じtenantでも別Projectの固定版・元IDを混ぜられない。"""
    row, _ = save(client, context)
    other = create_test_project()
    assign_project_role(user=context["user"], project=other, role_key="project_admin")
    base = f"/projects/{other.id}/artifact-releases"
    assert client.get(base + f"/{row['id']}").status_code == 404
    assert client.get(base + f"/{row['id']}/nodes").status_code == 404
    assert client.post(base + "/preview", json=preview_data(context)).status_code == 404
    wrong_version = preview_data(context, kind="requirement", include=False)
    wrong_version["source"]["version"] = 999
    assert client.post(base + "/preview", json=wrong_version).status_code == 404


def test_cursor_scope_tamper_and_large_page(client, db, context):
    """件数だけでなくbyte上限で分割し、他の版のcursorを拒否する。"""
    context["section"].content = "あ" * 200000
    context["req"].description = "い" * 200000
    db.commit()
    row, _ = save(client, context)
    response = client.get(f"{context['base']}/{row['id']}/nodes")
    assert response.status_code == 200 and len(response.content) <= MAX_PAGE_BYTES
    cursor = response.json()["next_cursor"]
    assert cursor
    assert len(all_nodes(client, context, row["id"])) == row["node_count"]
    other, _ = save(client, context)
    assert (
        client.get(
            f"{context['base']}/{other['id']}/nodes", params={"cursor": cursor}
        ).status_code
        == 400
    )
    assert (
        client.get(
            f"{context['base']}/{row['id']}/nodes", params={"cursor": "x" + cursor[1:]}
        ).status_code
        == 400
    )


def test_oversized_node_is_explicit_without_partial_save(client, db, context):
    """大きすぎる本文を黙って切らず413で返す。"""
    context["section"].content = "あ" * 400000
    db.commit()
    response = client.post(context["base"] + "/preview", json=preview_data(context))
    assert response.status_code == 413
    assert db.scalar(select(func.count()).select_from(ArtifactRelease)) == 0


def test_database_seal_rejects_changes_and_extra_nodes(client, db, context):
    """APIだけでなくDBも完成版の変更・削除・本文追加を拒否する。"""
    row, _ = save(client, context)
    rid = UUID(row["id"])
    node = db.scalar(
        select(ArtifactReleaseNode).where(ArtifactReleaseNode.release_id == rid)
    )
    assert node is not None
    operations = [
        update(ArtifactRelease).where(ArtifactRelease.id == rid).values(title="改変"),
        delete(ArtifactRelease).where(ArtifactRelease.id == rid),
        update(ArtifactReleaseNode)
        .where(ArtifactReleaseNode.id == node.id)
        .values(payload={}),
        delete(ArtifactReleaseNode).where(ArtifactReleaseNode.id == node.id),
        insert(ArtifactReleaseNode).values(
            id=uuid4(),
            release_id=rid,
            parent_id=None,
            ordinal=999,
            position=0,
            node_kind="requirement",
            source_id=1,
            payload={},
            content_digest="0" * 64,
        ),
    ]
    for statement in operations:
        with pytest.raises(DBAPIError) as exc:
            db.execute(statement)
            db.commit()
        assert getattr(exc.value.orig, "pgcode", None) == "55000"
        db.rollback()


def test_atomic_audit_failure_rolls_back(client, db, context, monkeypatch):
    """監査も同じtransactionで保存し、失敗時に固定版を部分保存しない。"""
    from app.services.audit_log import AuditLogService

    data = prepare(client, context)

    def fail(*args, **kwargs):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(AuditLogService, "record", fail)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        client.post(context["base"], json=data)
    assert db.scalar(select(func.count()).select_from(ArtifactRelease)) == 0
    assert db.scalar(select(func.count()).select_from(ArtifactReleaseNode)) == 0


def test_concurrent_identical_save_is_idempotent(client, db, context, monkeypatch):
    """同時保存はunique制約と再読込で1版・1操作記録に収束する。"""
    from app.main import create_app

    data = prepare(client, context)
    barrier = Barrier(2)
    original = service.builder.build

    def build(*args, **kwargs):
        result = original(*args, **kwargs)
        barrier.wait(timeout=15)
        return result

    monkeypatch.setattr(service.builder, "build", build)
    cookies, headers = dict(client.cookies), dict(client.headers)

    def call():
        with TestClient(create_app(), cookies=cookies, headers=headers) as peer:
            response = peer.post(context["base"], json=data)
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call(), range(2)))
    assert sorted(status for status, _ in results) == [200, 201], results
    assert len({result["id"] for _, result in results}) == 1
    assert db.scalar(select(func.count()).select_from(ArtifactRelease)) == 1


def test_repeatable_snapshot_does_not_mix_later_child_update(
    client, context, monkeypatch
):
    """読取開始後にcommitされた子変更を途中から混ぜない。"""
    from app.db.session import session_local

    data = prepare(client, context)
    original = service.builder._document_children

    def children(*args, **kwargs):
        with session_local() as concurrent:
            concurrent.execute(
                update(Requirement)
                .where(Requirement.id == context["req"].id)
                .values(title="後からcommitした本文", version=2)
            )
            concurrent.commit()
        return original(*args, **kwargs)

    monkeypatch.setattr(service.builder, "_document_children", children)
    response = client.post(context["base"], json=data)
    assert response.status_code == 201, response.text
    nodes = all_nodes(client, context, response.json()["id"])
    assert (
        next(n for n in nodes if n["node_kind"] == "requirement")["payload"]["title"]
        == "保存を続けられる"
    )


@pytest.mark.parametrize("kind", ["attachment", "test_design"])
def test_future_kinds_are_not_silently_accepted(client, context, kind):
    """後続の公開対象をこの単位で実装済みと見せない。"""
    data = preview_data(context)
    data["source"]["kind"] = kind
    assert client.post(context["base"] + "/preview", json=data).status_code == 422


def test_cross_tenant_release_and_source_cannot_mix(
    client, db, context, create_test_user, create_test_project, assign_project_role
):
    """両組織の実データを用意して、固定版・COUNT・元IDの境界を確認する。"""
    from app.models.tenant import Tenant, TenantMember

    own, _ = save(client, context)
    tenant = Tenant(name="Other tenant", slug="other-tenant")
    db.add(tenant)
    db.commit()
    peer = create_test_user(email="other-tenant@example.com")
    member = db.scalar(select(TenantMember).where(TenantMember.user_id == peer.id))
    assert member is not None
    member.tenant_id = tenant.id
    project = create_test_project()
    project.tenant_id = tenant.id
    db.commit()
    assign_project_role(user=peer, project=project, role_key="project_admin")
    specification = Specification(
        project_id=project.id,
        kind="api",
        title="他組織の本文",
        summary="非公開",
        content={"type": "api", "path": "/private"},
        created_by=peer.id,
        updated_by=peer.id,
    )
    db.add(specification)
    db.commit()
    authorize_as(client, peer)
    foreign_ctx = dict(
        project=project,
        spec=specification,
        base=f"/projects/{project.id}/artifact-releases",
    )
    foreign, _ = save(
        client,
        foreign_ctx,
        preview_data(foreign_ctx, kind="specification", include=False),
    )
    authorize_as(client, context["user"])
    assert client.get(context["base"]).json()["total"] == 1
    assert client.get(f"{context['base']}/{foreign['id']}").status_code == 404
    assert client.get(f"{context['base']}/{foreign['id']}/nodes").status_code == 404
    assert client.get(foreign_ctx["base"]).status_code == 403
    response = client.post(
        context["base"] + "/preview",
        json={
            "source": {"kind": "specification", "id": specification.id, "version": 999}
        },
    )
    assert response.status_code == 404 and "他組織" not in response.text
    assert client.get(f"{context['base']}/{own['id']}").status_code == 200


def test_same_release_parent_constraint(client, db, context):
    """DBの複合FKで別の固定版の親へつなぐことを拒否する。"""
    saved, _ = save(client, context)
    original = db.get(ArtifactRelease, UUID(saved["id"]))
    assert original is not None
    parent = db.scalar(
        select(ArtifactReleaseNode).where(ArtifactReleaseNode.release_id == original.id)
    )
    assert parent is not None
    header = {
        column.name: getattr(original, column.name)
        for column in ArtifactRelease.__table__.columns
    }
    header.update(id=uuid4(), request_id=uuid4(), sealed=False)
    db.add(ArtifactRelease(**header))
    db.flush()
    with pytest.raises(IntegrityError) as exc:
        db.execute(
            insert(ArtifactReleaseNode).values(
                id=uuid4(),
                release_id=header["id"],
                parent_id=parent.id,
                ordinal=0,
                position=0,
                node_kind="requirement",
                source_id=1,
                payload={},
                content_digest="0" * 64,
            )
        )
        db.commit()
    assert getattr(exc.value.orig, "pgcode", None) == "23503"
    db.rollback()


def test_current_source_metadata_does_not_follow_moved_document(
    client, db, context, create_test_project
):
    """元対象が別Projectへ移動しても、その現在版を覗かない。"""
    row, _ = save(
        client, context, preview_data(context, kind="requirement", include=False)
    )
    other = create_test_project()
    context["doc"].project_id = other.id
    db.commit()
    response = client.get(f"{context['base']}/{row['id']}")
    assert response.status_code == 200
    assert response.json()["source_state"] == "deleted"
    assert response.json()["current_source_version"] is None


def test_authentication_csrf_and_client_payload_are_not_bypassed(client, db, context):
    """新APIでも既存Cookie/CSRFを通り、本文・actorの偽装を受け取らない。"""
    from app.core.config import settings

    data = prepare(client, context)
    assert (
        client.post(context["base"], json={**data, "created_by": 999}).status_code
        == 422
    )
    assert client.post(context["base"], json={**data, "payload": {}}).status_code == 422
    client.headers.pop(settings.csrf_header_name)
    assert (
        client.post(context["base"], json=data).json()["code"] == "CSRF_TOKEN_INVALID"
    )
    client.cookies.clear()
    assert client.get(context["base"]).status_code == 401
    assert db.scalar(select(func.count()).select_from(ArtifactRelease)) == 0


def test_original_edits_reordering_and_specification_lifecycle_keep_snapshot(
    client, db, context, create_test_requirement_section
):
    """現在版の編集・章順変更・設計の削除/復元/purgeで固定本文を変えない。"""
    from app.repositories.trash import TrashRepository

    create_test_requirement_section(document=context["doc"], title="二つ目の章")
    row, _ = save(client, context)
    before = all_nodes(client, context, row["id"])
    context["section"].sort_order = 100
    context["section"].content = "現在版だけの変更"
    context["req"].title = "更新後の要件"
    context["req"].version += 1
    context["spec"].summary = "更新後の設計"
    db.commit()
    assert all_nodes(client, context, row["id"]) == before
    base = f"/projects/{context['project'].id}"
    url = base + f"/specifications/{context['spec'].id}"
    assert client.delete(url, params={"version": 1}).status_code == 204
    assert all_nodes(client, context, row["id"]) == before
    trash = client.get(base + "/trash", params={"kind": "specification"}).json()[
        "items"
    ][0]
    restored = client.post(
        base + f"/trash/specification/{context['spec'].id}/restore",
        json={"version": trash["version"], "deleted_at": trash["deleted_at"]},
    )
    assert restored.status_code == 204, restored.text
    assert all_nodes(client, context, row["id"]) == before
    repository = TrashRepository()
    targets = repository.purge_plan(db, "specifications", context["spec"].id)
    repository.purge(db, targets, repository.detachments(db, targets))
    db.commit()
    assert all_nodes(client, context, row["id"]) == before
    assert (
        client.get(f"{context['base']}/{row['id']}").json()["content_digest"]
        == row["content_digest"]
    )


@pytest.mark.parametrize(
    "state", ["session_revoked", "tenant_suspended", "project_removed"]
)
def test_access_rechecked_after_revocation(client, db, context, state):
    """保存後も本人の現在sessionと所属を単体・件数・本文・再送へ適用する。"""
    from app.models.project import ProjectMember
    from app.models.session import UserSession
    from app.models.tenant import TenantMember

    row, data = save(client, context)
    session_id = authorize_as(client, context["user"])
    if state == "session_revoked":
        session = db.get(UserSession, session_id)
        assert session is not None
        session.revoked_at = datetime.now(UTC)
    elif state == "tenant_suspended":
        member = db.scalar(
            select(TenantMember).where(
                TenantMember.user_id == context["user"].id,
                TenantMember.tenant_id == context["project"].tenant_id,
            )
        )
        assert member is not None
        member.status = "suspended"
    else:
        db.execute(
            delete(ProjectMember).where(
                ProjectMember.project_id == context["project"].id,
                ProjectMember.user_id == context["user"].id,
            )
        )
    db.commit()
    for suffix in ["", f"/{row['id']}", f"/{row['id']}/nodes"]:
        assert client.get(context["base"] + suffix).status_code in (401, 403)
    assert client.post(context["base"], json=data).status_code in (401, 403)


def test_tenant_admin_without_project_membership_cannot_read(client, db, context):
    """組織管理権限をProject固定版への迂回路にしない。"""
    from app.models.project import ProjectMember
    from app.models.tenant import TenantMember

    row, data = save(client, context)
    member = db.scalar(
        select(TenantMember).where(TenantMember.user_id == context["user"].id)
    )
    assert member is not None
    member.role_id = db.scalar(
        select(Role.id).where(Role.key == "tenant_admin", Role.scope == "tenant")
    )
    db.execute(
        delete(ProjectMember).where(
            ProjectMember.project_id == context["project"].id,
            ProjectMember.user_id == context["user"].id,
        )
    )
    db.commit()
    for suffix in ["", f"/{row['id']}", f"/{row['id']}/nodes"]:
        assert client.get(context["base"] + suffix).status_code == 403
    assert client.post(context["base"], json=data).status_code == 403
