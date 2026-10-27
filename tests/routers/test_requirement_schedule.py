"""日程参照の一意件数・取得境界・現在の認可を検証する。"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, event, select

from app.core.config import settings
from app.models.project import ProjectMember
from app.models.rbac import Permission, RolePermission
from app.models.task import Milestone, RequirementTaskRelation
from app.models.tenant import TenantMember
from tests.helpers.auth import authorize_as
from tests.routers.test_project_objectives import objective_setup as objective_setup


@pytest.fixture
def schedule_setup(objective_setup, create_test_requirement, monkeypatch):
    """実認可と一時DBだけを使用する。"""
    monkeypatch.setattr(settings, "project_overview_schedule_enabled", True)

    def make(role="project_admin"):
        c = objective_setup(role)
        c.req = create_test_requirement(document=c.doc)
        c.url = f"/projects/{c.project.id}/requirement-schedule"
        return c

    return make


@pytest.mark.parametrize("role", ["project_admin", "manager", "member", "viewer"])
def test_schedule_roles_flag_metadata_and_no_writes(
    client, schedule_setup, monkeypatch, role
):
    """読取権限で参照でき、flag/metadataで不要な行を取得しない。"""
    c = schedule_setup(role)
    meta = client.get(c.url, params={"metadata_only": True})
    assert meta.status_code == 200, meta.text
    assert meta.headers["cache-control"] == "no-store"
    assert meta.json()["enabled"] and meta.json()["groups"] == []
    assert client.post(c.url, json={}).status_code == 405
    monkeypatch.setattr(settings, "project_overview_schedule_enabled", False)
    disabled = client.get(c.url).json()
    assert not disabled["enabled"] and disabled["summary"] is None
    assert disabled["total"] is None and disabled["common_total"] is None


def test_schedule_unique_tasks_filters_dates_and_deleted_sources(
    client,
    schedule_setup,
    db,
    create_test_task,
    create_test_requirement,
):
    """共有Task/親子・未日程・未関連を保持し本文を返さない。"""
    c = schedule_setup()
    second = create_test_requirement(document=c.doc)
    task = create_test_task(project=c.project, title="shared")
    task.start_date = date.today() - timedelta(days=5)
    task.due_date = date.today() - timedelta(days=1)
    task.description = "MUST_NOT_RETURN_PRIVATE_BODY"
    child = create_test_task(project=c.project)
    child.parent_task_id = task.id
    child.due_date = date.today()
    for req in (c.req, second):
        db.add(
            RequirementTaskRelation(
                requirement_id=req.id, task_id=task.id, relation_type="implements"
            )
        )
    db.add(
        RequirementTaskRelation(
            requirement_id=c.req.id, task_id=child.id, relation_type="tests"
        )
    )
    db.commit()
    response = client.get(c.url)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["summary"] == {"total": 2, "planned": 1, "unplanned": 1, "overdue": 1}
    assert sum(g["task_count"] for g in data["groups"]) == 3
    assert "MUST_NOT_RETURN_PRIVATE_BODY" not in response.text
    assert data["common_total"] == 0
    filtered = client.get(c.url, params={"q": c.req.requirement_code}).json()
    assert filtered["total"] == 1 and filtered["summary"]["total"] == 2
    assert filtered["common_state"] == "outside_requirement_filter"
    assert filtered["common_total"] is None
    c.doc.deleted_at = datetime.now(UTC)
    db.commit()
    deleted = client.get(c.url).json()
    assert deleted["total"] == 0 and deleted["common_total"] == 2


def test_schedule_bounds_milestones_and_constant_query_count(
    client,
    schedule_setup,
    db,
    create_test_task,
    create_test_requirement,
):
    """20要件/10参照/20共通/50節目、ページ外も含む件数。"""
    c = schedule_setup()
    for i in range(25):
        req = c.req if i == 0 else create_test_requirement(document=c.doc)
        for j in range(12):
            t = create_test_task(project=c.project)
            db.add(
                RequirementTaskRelation(
                    requirement_id=req.id, task_id=t.id, relation_type="implements"
                )
            )
    for _ in range(22):
        create_test_task(project=c.project)
    for i in range(52):
        db.add(
            Milestone(project_id=c.project.id, title=f"M{i}", target_date=date.today())
        )
    db.commit()
    statements = []

    def observe(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", observe)
    try:
        response = client.get(c.url)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", observe)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 25 and len(data["groups"]) == 20
    assert all(
        g["task_count"] == 12 and len(g["tasks"]) == 10 and g["has_more"]
        for g in data["groups"]
    )
    assert data["summary"]["total"] == 322
    assert data["common_total"] == 22 and len(data["common_tasks"]) == 20
    assert len(data["milestones"]) == 50 and data["milestones_has_more"]
    assert len(statements) < 50
    last = client.get(c.url, params={"page": 2, "common_page": 2}).json()
    assert len(last["groups"]) == 5 and len(last["common_tasks"]) == 2


@pytest.mark.parametrize("code", ["task:read", "requirement:read"])
def test_schedule_permission_revocation_hides_all_collections(
    client, schedule_setup, db, code
):
    """名称/件数/節目をフロントの表示制御だけに任せない。"""
    c = schedule_setup("viewer")
    role_id = db.scalar(
        select(ProjectMember.role_id).where(
            ProjectMember.project_id == c.project.id, ProjectMember.user_id == c.user.id
        )
    )
    permission = select(Permission.id).where(Permission.code == code)
    db.execute(
        delete(RolePermission).where(
            RolePermission.role_id == role_id,
            RolePermission.permission_id.in_(permission),
        )
    )
    db.commit()
    assert client.get(c.url).status_code == 403
    assert client.get(c.url, params={"metadata_only": True}).status_code == 403


def test_schedule_membership_tenant_guest_and_deleted_project(
    client,
    schedule_setup,
    db,
    create_test_user,
):
    """system_adminだけ・組織参加失効・guest・他Tenantを拒否。"""
    c = schedule_setup()
    stranger = create_test_user(
        email="schedule-operator@example.com", system_role="system_admin"
    )
    authorize_as(client, stranger)
    assert client.get(c.url).status_code == 403
    authorize_as(client, c.user)
    client.headers["X-Tenant-ID"] = "2147483647"
    assert client.get(c.url).status_code in (403, 404)
    client.headers.pop("X-Tenant-ID")
    c.user.user_type = "guest"
    db.commit()
    assert client.get(c.url).status_code == 403
    c.user.user_type = "internal"
    membership = db.scalar(
        select(TenantMember).where(
            TenantMember.user_id == c.user.id,
            TenantMember.tenant_id == c.project.tenant_id,
        )
    )
    membership.status = "inactive"
    db.commit()
    assert client.get(c.url).status_code == 403
    membership.status = "active"
    c.project.deleted_at = datetime.now(UTC)
    db.commit()
    assert client.get(c.url).status_code in (403, 404)
    c.project.deleted_at = None
    db.execute(
        delete(ProjectMember).where(
            ProjectMember.project_id == c.project.id, ProjectMember.user_id == c.user.id
        )
    )
    db.commit()
    assert client.get(c.url).status_code == 403


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("2028-02-28", "2028-03-01", 200),
        ("2026-12-28", "2027-01-10", 200),
        ("2026-01-02", "2026-01-01", 400),
        ("2026-01-01", "2026-04-01", 400),
        ("bad", "2026-01-01", 422),
        ("9999-12-31", None, 200),
    ],
)
def test_schedule_date_boundaries(client, schedule_setup, start, end, expected):
    """日付正本を時刻化せず、無制限期間や500を防ぐ。"""
    c = schedule_setup()
    params = {"window_start": start}
    if end:
        params["window_end"] = end
    assert client.get(c.url, params=params).status_code == expected
