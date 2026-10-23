"""追跡の正本・認可・取得上限・行数に比例しないSQLを検証する。"""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select

from app.core.config import settings
from app.models.project import Project
from app.models.rbac import Permission, RolePermission
from app.models.requirement import Requirement
from app.models.specification import Specification, SpecificationLink
from app.models.task import RequirementTaskRelation
from app.models.tenant import Tenant
from app.models.test_design import RequirementTestItem, TestDesign, TestItem
from app.repositories.requirement_trace import RequirementTraceRepository
from tests.helpers.auth import authorize_as
from tests.routers.test_project_objectives import objective_setup as objective_setup


@pytest.fixture
def trace_setup(objective_setup, create_test_requirement, monkeypatch):
    """A3aと同じ実認可のfixtureを使い、新規GETだけを有効化する。"""
    monkeypatch.setattr(settings, "project_traceability_enabled", True)

    def make(role="project_admin"):
        c = objective_setup(role)
        c.req = create_test_requirement(document=c.doc)
        c.url = f"/projects/{c.project.id}/requirement-trace"
        return c

    return make


@pytest.mark.parametrize("role", ["project_admin", "manager", "member", "viewer"])
def test_trace_roles_read_only_and_no_evaluation(client, trace_setup, role):
    """読取を編集権限と混同せず、未集計を未合意/未実施にしない。"""
    c = trace_setup(role)
    response = client.get(c.url)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["state"] == "ready" and data["total"] == 1
    row = data["items"][0]
    assert row["id"] == c.req.id and row["version"] == c.req.version
    assert "description" not in row and "comments" not in row
    assert row["agreement_state"] == row["execution_state"] == "not_evaluated"
    assert row["tasks"]["count"] == 0
    assert client.get(f"{c.url}/{c.req.id}").status_code == 200
    assert client.post(c.url, json={}).status_code == 405


def test_trace_domain_and_requirement_restrictions(client, trace_setup, db):
    """分野readなしでは件数・名称・存在を返さない。"""
    c = trace_setup("viewer")
    # UserのORM関係に依存せず、明示されたProjectのroleを取得する。
    from app.models.project import ProjectMember

    role_id = db.scalar(
        select(ProjectMember.role_id).where(
            ProjectMember.project_id == c.project.id, ProjectMember.user_id == c.user.id
        )
    )
    codes = ["document:read", "task:read", "test_plan:read", "artifact_release:read"]
    ids = select(Permission.id).where(Permission.code.in_(codes))
    db.execute(
        delete(RolePermission).where(
            RolePermission.role_id == role_id, RolePermission.permission_id.in_(ids)
        )
    )
    db.commit()
    for data in [
        client.get(c.url).json()["items"][0],
        client.get(f"{c.url}/{c.req.id}").json(),
    ]:
        for key in ["designs", "tasks", "test_items"]:
            assert data[key] == {
                "state": "restricted",
                "count": None,
                "items": [],
                "has_more": False,
            }
    assert not client.get(c.url).json()["capabilities"]["read_approvals"]
    rid = db.scalar(select(Permission.id).where(Permission.code == "requirement:read"))
    db.execute(
        delete(RolePermission).where(
            RolePermission.role_id == role_id, RolePermission.permission_id == rid
        )
    )
    db.commit()
    data = client.get(c.url).json()
    assert (
        data["state"] == "restricted" and data["items"] == [] and data["total"] is None
    )
    assert c.doc.title not in json.dumps(data)
    assert client.get(f"{c.url}/{c.req.id}").status_code == 403


def test_trace_same_project_deleted_parents_and_nonmember(
    client,
    trace_setup,
    db,
    create_test_project,
    create_test_requirement_document,
    create_test_requirement,
    create_test_user,
):
    """IDを差し替えても他案件・削除済み文書の本文を返さない。"""
    c = trace_setup()
    other = create_test_project()
    doc = create_test_requirement_document(project=other)
    req = create_test_requirement(document=doc)
    assert client.get(f"{c.url}/{req.id}").status_code == 404
    assert client.get(c.url, params={"document_id": doc.id}).json()["total"] == 0
    assert client.get(f"/projects/{other.id}/requirement-trace").status_code == 403
    c.doc.deleted_at = datetime.now(UTC)
    db.commit()
    assert client.get(c.url).json()["total"] == 0
    assert client.get(f"{c.url}/{c.req.id}").status_code == 404
    outsider = create_test_user(email="trace-outsider@example.com")
    authorize_as(client, outsider)
    assert client.get(c.url).status_code == 403


def test_trace_relations_exclude_foreign_deleted_and_spacers(
    client,
    trace_setup,
    db,
    create_test_task,
    create_test_requirement,
    create_test_project,
):
    """3正本の同案件・有効性を確認し、共有Taskを件数へ重複加算しない。"""
    c = trace_setup()
    second = create_test_requirement(document=c.doc)
    task = create_test_task(project=c.project)
    other = create_test_project()
    foreign = create_test_task(project=other)
    spec = Specification(
        project_id=c.project.id,
        kind="screen",
        title="画面設計",
        content={},
        created_by=c.user.id,
        updated_by=c.user.id,
    )
    design = TestDesign(
        project_id=c.project.id,
        name="テスト設計",
        created_by=c.user.id,
        updated_by=c.user.id,
    )
    db.add_all([spec, design])
    db.flush()
    item = TestItem(id=uuid4(), design_id=design.id, code="T001", content="確認する")
    spacer = TestItem(id=uuid4(), design_id=design.id, code="SPACE", is_spacer=True)
    db.add_all([item, spacer])
    db.flush()
    db.add_all(
        [
            RequirementTaskRelation(
                requirement_id=c.req.id, task_id=task.id, relation_type="implementation"
            ),
            RequirementTaskRelation(
                requirement_id=second.id,
                task_id=task.id,
                relation_type="implementation",
            ),
            RequirementTaskRelation(
                requirement_id=c.req.id,
                task_id=foreign.id,
                relation_type="implementation",
            ),
            SpecificationLink(specification_id=spec.id, requirement_id=c.req.id),
            RequirementTestItem(
                requirement_id=c.req.id, item_id=item.id, created_by=c.user.id
            ),
            RequirementTestItem(
                requirement_id=c.req.id, item_id=spacer.id, created_by=c.user.id
            ),
        ]
    )
    db.commit()
    data = client.get(f"{c.url}/{c.req.id}").json()
    assert (
        data["tasks"]["count"]
        == data["designs"]["count"]
        == data["test_items"]["count"]
        == 1
    )
    assert data["test_items"]["items"][0]["design_id"] == design.id
    assert data["tasks"]["items"][0]["id"] == str(task.id)
    design.deleted_at = spec.deleted_at = task.deleted_at = datetime.now(UTC)
    db.commit()
    data = client.get(f"{c.url}/{c.req.id}").json()
    assert all(data[key]["count"] == 0 for key in ["tasks", "designs", "test_items"])


def test_trace_bounded_details_and_more(client, trace_setup, db, create_test_task):
    """抜粋と関連20件の上限を明示し、全文/全graphを取得しない。"""
    c = trace_setup()
    c.req.description = "長" * 8001
    c.req.acceptance_criteria = "条" * 8000
    tasks = [create_test_task(project=c.project) for _ in range(23)]
    db.add_all(
        [
            RequirementTaskRelation(
                requirement_id=c.req.id, task_id=t.id, relation_type="implementation"
            )
            for t in tasks
        ]
    )
    db.commit()
    response = client.get(f"{c.url}/{c.req.id}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["description"]) == 8000 and data["description_truncated"]
    assert (
        len(data["acceptance_criteria"]) == 8000
        and not data["acceptance_criteria_truncated"]
    )
    assert (
        data["tasks"]["count"] == 23
        and len(data["tasks"]["items"]) == 20
        and data["tasks"]["has_more"]
    )
    assert len(response.content) < 512 * 1024


def test_trace_paging_search_and_constant_sql(client, trace_setup, db):
    """1000要件の20/100行で集約のSQLが比例増加しない。"""
    c = trace_setup()
    db.add_all(
        [
            Requirement(
                document_id=c.doc.id,
                requirement_code=f"REQ-{i:04}",
                title=f"追跡{i}",
                requirement_type="functional",
                description="本文は一覧へ返さない",
            )
            for i in range(1000)
        ]
    )
    db.commit()
    assert client.get(c.url, params={"q": "追跡999"}).json()["total"] == 1
    repository = RequirementTraceRepository()
    project_id = c.project.id
    engine = db.get_bind()
    statements = []

    def count(*args):
        statements.append(args[2])

    event.listen(engine, "before_cursor_execute", count)
    try:
        counts = []
        for size in [20, 100]:
            statements.clear()
            rows, total = repository.rows(db, project_id, 1, size, None, None, None)
            assert total == 1001 and len(rows) == size
            for kind in ["designs", "tasks", "test_items"]:
                repository.counts(db, project_id, [r["id"] for r in rows], kind)
            counts.append(len(statements))
        assert counts == [5, 5]
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert client.get(c.url, params={"page_size": 100}).json()["page_size"] == 100
    for params in [{"page_size": 101}, {"page": 0}, {"q": "x" * 201}]:
        assert client.get(c.url, params=params).status_code == 422


def test_trace_disabled_and_guest(client, trace_setup, db, monkeypatch):
    """flagで新規読取を止め、外部本人へ内部一覧を開放しない。"""
    c = trace_setup()
    monkeypatch.setattr(settings, "project_traceability_enabled", False)
    data = client.get(c.url).json()
    assert data["state"] == "disabled" and not data["enabled"] and data["items"] == []
    assert client.get(f"{c.url}/{c.req.id}").status_code == 404
    monkeypatch.setattr(settings, "project_traceability_enabled", True)
    c.user.user_type = "guest"
    db.commit()
    assert client.get(c.url).status_code == 403


def test_trace_tenant_and_system_management_cannot_bypass(
    client, trace_setup, create_test_user, db
):
    """新経路でも別tenantと非参加の運営管理者を拒否する。"""
    c = trace_setup()
    tenant = Tenant(name="Other trace tenant", slug="other-trace-tenant")
    db.add(tenant)
    db.flush()
    project = Project(tenant_id=tenant.id, project_code="PRIVATE", name="Private")
    db.add(project)
    db.commit()
    url = c.url
    detail = f"{url}/{c.req.id}"
    assert client.get(f"/projects/{project.id}/requirement-trace").status_code == 403
    assert (
        client.get(f"/projects/{project.id}/requirement-trace/{c.req.id}").status_code
        == 403
    )
    operator = create_test_user(
        email="trace-operator@example.com", system_role="system_admin"
    )
    authorize_as(client, operator)
    assert client.get(url).status_code == 403
    assert client.get(detail).status_code == 403
