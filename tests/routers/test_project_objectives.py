"""目標正本・案件共通参照・履歴と認可の代表契約。"""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models.artifact_release import ArtifactRelease
from app.models.project import Project
from app.models.project_goal_source_event import ProjectGoalSourceEvent
from app.models.rbac import Permission, Role, RolePermission
from app.models.requirement import RequirementDocument
from app.models.tenant import Tenant
from app.routers import requirements_shared
from app.services.audit_log import AuditLogService
from app.services.guest_approval_scope import compare_current
from tests.helpers.auth import authorize_as


@pytest.fixture
def objective_setup(
    client,
    create_test_user,
    create_test_project,
    create_test_requirement_document,
    assign_project_role,
    monkeypatch,
):
    """通常DBを利用せず、本人と案件の認可を作る。"""
    monkeypatch.setattr(settings, "project_objectives_enabled", True)

    def make(role="project_admin"):
        user = create_test_user()
        project = create_test_project()
        doc = create_test_requirement_document(project=project)
        assign_project_role(user=user, project=project, role_key=role)
        authorize_as(client, user)
        return SimpleNamespace(
            user=user,
            project=project,
            doc=doc,
            url=f"/projects/{project.id}/objectives",
        )

    return make


def choose(client, c, *, version=1, **extra):
    """確認した出典版を送る。"""
    return client.patch(
        c.url,
        json={
            "version": version,
            "document_id": c.doc.id,
            "expected_document_version": c.doc.version,
            **extra,
        },
    )


@pytest.mark.parametrize(
    "role,status",
    [("project_admin", 200), ("manager", 200), ("member", 403), ("viewer", 403)],
)
def test_source_selection_permissions(client, objective_setup, role, status):
    """文書編集権限と共通指定権限を混同しない。"""
    c = objective_setup(role)
    assert client.get(c.url).json()["state"] == "unselected"
    result = choose(client, c)
    assert result.status_code == status
    if status == 200:
        assert result.json()["document"]["id"] == c.doc.id
        assert result.headers["cache-control"] == "no-store"


def test_source_history_noop_conflict_and_clear(client, objective_setup, db):
    """参照専用版を使い、理由付き解除と変更履歴を残す。"""
    c = objective_setup()
    before = (c.project.version, c.project.updated_at, c.project.updated_by)
    assert choose(client, c).json()["version"] == 2
    assert choose(client, c, version=2).json()["version"] == 2
    assert choose(client, c).status_code == 409
    assert (
        client.patch(c.url, json={"version": 2, "document_id": None}).status_code == 400
    )
    assert (
        client.patch(
            c.url, json={"version": 2, "document_id": None, "reason": "出典を見直す"}
        ).json()["state"]
        == "unselected"
    )
    history = client.get(c.url + "/events").json()
    assert history["total"] == 2
    assert history["items"][0]["changed_by_name"] == c.user.name
    assert history["items"][0]["reason"] == "出典を見直す"
    db.expire_all()
    project = db.get(Project, c.project.id)
    assert (project.version, project.updated_at, project.updated_by) == before
    assert project.goal_source_version == 3


def test_restricted_read_hides_source_and_history(client, objective_setup, db):
    """案件閲覧だけでは文書ID・本文・参照版を出さない。"""
    c = objective_setup("viewer")
    permission = db.scalar(
        select(Permission.id).where(Permission.code == "requirement:read")
    )
    role = db.scalar(
        select(Role.id).where(Role.key == "viewer", Role.scope == "project")
    )
    db.execute(
        delete(RolePermission).where(
            RolePermission.role_id == role, RolePermission.permission_id == permission
        )
    )
    db.commit()
    data = client.get(c.url).json()
    assert (
        data["state"] == "restricted"
        and data["document"] is None
        and data["version"] is None
    )
    assert not data["can_manage_source"]
    assert client.get(c.url + "/events").status_code == 403


def test_foreign_document_and_nonmember_rejected(
    client, objective_setup, create_test_project, create_test_requirement_document
):
    """同tenantでも別案件の文書/非参加案件を使わせない。"""
    c = objective_setup()
    other = create_test_project(project_code="OTHER")
    doc = create_test_requirement_document(project=other)
    response = client.patch(
        c.url,
        json={
            "version": 1,
            "document_id": doc.id,
            "expected_document_version": doc.version,
        },
    )
    assert response.status_code == 404
    assert doc.title not in response.text
    assert client.get(f"/projects/{other.id}/objectives").status_code == 403


def test_document_fields_are_optional_preserved_and_versioned(
    client, objective_setup, db, monkeypatch
):
    """purposeを変えず新項目を部分更新し、旧クライアントは保持する。"""
    c = objective_setup("member")
    url = f"/projects/{c.project.id}/requirement-documents/{c.doc.id}"
    c.doc.purpose = "既存の文書目的"
    db.commit()
    updated = client.patch(
        url,
        json={
            "version": c.doc.version,
            "project_goal": "案件のゴール",
            "project_scope": "  ",
            "project_completion_criteria": "確認条件",
        },
    )
    assert updated.status_code == 200
    data = updated.json()
    assert data["project_scope"] is None and data["purpose"] == "既存の文書目的"
    monkeypatch.setattr(settings, "project_objectives_enabled", False)
    old = client.patch(
        url, json={"version": data["version"], "title": "従来からの文書更新"}
    )
    assert old.status_code == 200 and old.json()["project_goal"] == "案件のゴール"
    assert (
        client.patch(
            url, json={"version": old.json()["version"], "project_goal": None}
        ).status_code
        == 403
    )
    assert client.get(c.url).json()["state"] == "disabled"


def test_fields_length_and_document_version_guard(client, objective_setup):
    """上限超過・選択前の文書変更・本文の混入を拒否する。"""
    c = objective_setup()
    url = f"/projects/{c.project.id}/requirement-documents/{c.doc.id}"
    assert (
        client.patch(
            url, json={"version": c.doc.version, "project_goal": "a" * 20001}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            c.url,
            json={
                "version": 1,
                "document_id": c.doc.id,
                "expected_document_version": c.doc.version + 1,
            },
        ).status_code
        == 409
    )
    assert choose(client, c, project_goal="ここでは編集不可").status_code == 422


def test_source_and_audit_failure_roll_back_together(
    client, objective_setup, monkeypatch, db
):
    """証跡保存に失敗した参照変更を確定しない。"""
    c = objective_setup()

    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(AuditLogService, "record", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        choose(client, c)
    assert client.get(c.url).json()["state"] == "unselected"
    assert list(db.scalars(select(ProjectGoalSourceEvent))) == []


def test_document_history_failure_rolls_back_body(
    client, objective_setup, monkeypatch, db
):
    """履歴保存に失敗した本文を残さない。"""
    c = objective_setup()
    version = c.doc.version

    def fail(*args, **kwargs):
        raise RuntimeError("history unavailable")

    monkeypatch.setattr(
        requirements_shared.document_service.change_log_service, "record", fail
    )
    with pytest.raises(RuntimeError, match="history unavailable"):
        client.patch(
            f"/projects/{c.project.id}/requirement-documents/{c.doc.id}",
            json={"version": version, "project_goal": "残してはいけない本文"},
        )
    db.expire_all()
    doc = db.get(RequirementDocument, c.doc.id)
    assert doc.project_goal is None and doc.version == version


def test_source_soft_delete_restore_and_db_guards(client, objective_setup, db):
    """削除後は参照不可、同じIDの復元後は同じ出典。履歴改変拒否。"""
    c = objective_setup()
    assert choose(client, c).status_code == 200
    from datetime import UTC, datetime

    c.doc.deleted_at = datetime.now(UTC)
    db.commit()
    assert client.get(c.url).json()["state"] == "unavailable"
    c.doc.deleted_at = None
    db.commit()
    assert client.get(c.url).json()["document"]["id"] == c.doc.id
    with pytest.raises(IntegrityError):
        db.execute(text("UPDATE project_goal_source_events SET reason='overwrite'"))
    db.rollback()


def test_v1_retained_and_v2_preserves_objectives(client, objective_setup, db):
    """旧版を変更せず、新本文の固定・公開と旧比較を一貫させる。"""
    c = objective_setup()
    releases = f"/projects/{c.project.id}/artifact-releases"
    publications = f"/projects/{c.project.id}/artifact-publications"
    source = {"kind": "requirement_document", "id": c.doc.id, "version": c.doc.version}
    old = client.post(releases + "/preview", json={"source": source}).json()
    saved = client.post(
        releases,
        json={
            "source": source,
            "request_id": str(uuid4()),
            "expected_scope_digest": old["scope_digest"],
        },
    )
    assert saved.status_code == 201
    release = db.get(ArtifactRelease, UUID(saved.json()["id"]))
    assert compare_current(db, c.project.id, release) == "equal"
    changed = client.patch(
        f"/projects/{c.project.id}/requirement-documents/{c.doc.id}",
        json={
            "version": c.doc.version,
            "project_goal": "共通ゴール",
            "project_scope": "対象範囲",
            "project_completion_criteria": "全員で確認",
        },
    )
    assert changed.status_code == 200
    source["version"] = changed.json()["version"]
    rejected = client.post(releases + "/preview", json={"source": source})
    assert rejected.status_code == 409
    assert "OBJECTIVE_PROJECTION_UPGRADE_REQUIRED" in rejected.text
    db.expire_all()
    assert compare_current(db, c.project.id, release) == "changed"
    request = {"source": source, "projection_version": "internal-preview-v2"}
    preview = client.post(releases + "/preview", json=request)
    assert preview.status_code == 200
    fixed = client.post(
        releases,
        json={
            **request,
            "request_id": str(uuid4()),
            "expected_scope_digest": preview.json()["scope_digest"],
        },
    )
    assert fixed.status_code == 201
    release_id = fixed.json()["id"]
    nodes = client.get(releases + f"/{release_id}/nodes").json()["items"]
    assert nodes[0]["payload"]["project_goal"] == "共通ゴール"
    pub_request = {
        "release_id": release_id,
        "request_id": str(uuid4()),
        "projection_version": "public-preview-v2",
    }
    preview = client.post(publications + "/preview", json=pub_request)
    assert preview.status_code == 200
    body = preview.json()
    pub = client.post(
        publications,
        json={
            **pub_request,
            "expected_public_digest": body["public_digest"],
            "expected_source_manifest_digest": body["source_manifest_digest"],
        },
    )
    assert pub.status_code == 201
    public = client.get(publications + f"/{pub.json()['content']['id']}/nodes").json()
    assert public["projection_version"] == "public-preview-v2"
    assert public["items"][0]["payload"]["project_completion_criteria"] == "全員で確認"
    assert (
        client.get(releases + f"/{saved.json()['id']}/nodes")
        .json()["items"][0]["payload"]
        .get("project_goal")
        is None
    )


def test_management_and_other_tenant_do_not_bypass_business_access(
    client, objective_setup, create_test_user, db
):
    """運営/組織管理者のメタデータ権限で目標や履歴を開かない。"""
    c = objective_setup()
    other_tenant = Tenant(name="Other tenant", slug="other-objective-tenant")
    db.add(other_tenant)
    db.flush()
    project = Project(
        tenant_id=other_tenant.id, project_code="OTHER", name="Private project"
    )
    db.add(project)
    db.commit()
    assert client.get(f"/projects/{project.id}/objectives").status_code == 403
    operator = create_test_user(
        email="operator@example.com", system_role="system_admin"
    )
    authorize_as(client, operator)
    assert client.get(c.url).status_code == 403
    assert client.get(c.url + "/events").status_code == 403
    assert choose(client, c).status_code == 403


def test_database_rejects_cross_project_source(
    objective_setup, create_test_project, create_test_requirement_document, db
):
    """ORMメタデータに逆参照を持たせなくても、実DBの複合FKが拒否する。"""
    c = objective_setup()
    other = create_test_project()
    document = create_test_requirement_document(project=other)
    with pytest.raises(IntegrityError):
        db.execute(
            text("UPDATE projects SET goal_document_id=:document WHERE id=:project"),
            {"document": document.id, "project": c.project.id},
        )
    db.rollback()
