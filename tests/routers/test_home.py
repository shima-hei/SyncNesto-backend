"""HOMEの本人・所属・権限・期限・集計・表示上限を検証する。"""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.models.rbac import Permission, Role, RolePermission
from app.models.task import Task
from app.repositories.rbac import RbacRepository
from app.services.home import home_today
from tests.helpers.auth import authorize_as

TODAY = date(2026, 9, 29)


@pytest.fixture
def home_context(
    client, db, create_test_user, create_test_project, assign_project_role, monkeypatch
):
    """時計を固定し、所属2案件と未所属案件を用意する。"""
    monkeypatch.setattr("app.services.home.home_today", lambda timezone: TODAY)
    user = create_test_user()
    other = create_test_user(email="other@example.com")
    projects = [create_test_project(name=name) for name in ("案件A", "案件B", "未所属")]
    members = [
        assign_project_role(user=user, project=project, role_key="member")
        for project in projects[:2]
    ]
    authorize_as(client, user)

    def task(
        *,
        project=None,
        assignee=None,
        due=None,
        status="todo",
        priority="medium",
        deleted=False,
    ):
        row = Task(
            project_id=(project or projects[0]).id,
            assignee_id=(assignee or user).id,
            task_code=f"TASK-{db.query(Task).count() + 1}",
            title="確認作業",
            status=status,
            priority=priority,
            due_date=due,
            deleted_at=datetime.now(UTC) if deleted else None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    return dict(user=user, other=other, projects=projects, members=members, task=task)


def test_tasks_are_personal_unfinished_and_sorted(client, home_context):
    """期限区分・期限・同一期限の優先度・IDで安定して並ぶ。"""
    ctx = home_context
    task = ctx["task"]
    undated = task()
    later = task(due=TODAY + timedelta(days=14))
    soon = task(due=TODAY + timedelta(days=7))
    today_low = task(due=TODAY, priority="low")
    today_high = task(due=TODAY, priority="high")
    overdue = task(due=TODAY - timedelta(days=1))
    task(assignee=ctx["other"], due=TODAY)
    task(status="done", due=TODAY - timedelta(days=1))
    task(status="cancelled", due=TODAY - timedelta(days=1))
    task(deleted=True, due=TODAY)
    task(project=ctx["projects"][2], due=TODAY)
    response = client.get("/home/tasks?limit=10")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["id"] for row in body["items"]] == [
        row.id for row in (overdue, today_high, today_low, soon, later, undated)
    ]
    assert body["today"] == "2026-09-29"
    assert body["summary"] == dict(total=6, overdue=1, due_today=2, due_soon=1)
    assert body["items"][0]["project_name"] == "案件A"
    limited = client.get("/home/tasks?limit=2").json()
    assert len(limited["items"]) == 2
    assert limited["summary"] == body["summary"]


def test_project_statistics_and_future_deadline(client, home_context):
    """完了はdoneのみ。中止・削除を期限対象に含めない。"""
    ctx = home_context
    task = ctx["task"]
    task(due=TODAY - timedelta(days=1))
    task(due=TODAY + timedelta(days=2))
    task(assignee=ctx["other"], due=TODAY)
    task(status="done", due=TODAY - timedelta(days=2))
    task(status="cancelled", due=TODAY)
    task(deleted=True, due=TODAY)
    task(project=ctx["projects"][2], due=TODAY)
    response = client.get("/home/projects")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert body["items"][0]["tasks"] == dict(
        my_open_count=2,
        overdue_count=1,
        done_count=1,
        total_count=5,
        next_due_date="2026-09-29",
    )
    assert body["items"][1]["tasks"] == dict(
        my_open_count=0,
        overdue_count=0,
        done_count=0,
        total_count=0,
        next_due_date=None,
    )
    limited = client.get("/home/projects?limit=1").json()
    assert limited["total"] == 2
    assert len(limited["items"]) == 1


def test_removed_membership_and_deleted_projects_are_excluded(client, db, home_context):
    """所属解除や案件削除でタスク・件数の両方から消える。"""
    ctx = home_context
    for project in ctx["projects"]:
        ctx["task"](project=project, due=TODAY)
    ctx["members"][0].deleted_at = datetime.now(UTC)
    ctx["projects"][1].deleted_at = datetime.now(UTC)
    db.commit()
    assert client.get("/home/tasks").json()["summary"]["total"] == 0
    assert client.get("/home/projects").json()["total"] == 0


def test_project_read_does_not_grant_task_statistics(client, db, home_context):
    """project:readだけの所属にはタスク情報を返さない。"""
    ctx = home_context
    role = Role(key="home_project_only", name="案件閲覧", scope="project")
    db.add(role)
    db.flush()
    permission = db.query(Permission).filter(Permission.code == "project:read").one()
    db.add(RolePermission(role_id=role.id, permission_id=permission.id))
    for member in ctx["members"]:
        member.role_id = role.id
    db.commit()
    ctx["task"](due=TODAY)
    assert client.get("/home/tasks").json()["summary"]["total"] == 0
    body = client.get("/home/projects").json()
    assert body["total"] == 2
    assert all(project["tasks"] is None for project in body["items"])


def test_system_admin_still_needs_membership_in_home(client, db, home_context):
    """管理一覧と参加案件を区別し、既存APIの既定動作を維持する。"""
    ctx = home_context
    repository = RbacRepository()
    role = repository.get_role_by_key_scope(db, key="system_admin", scope="system")
    assert role is not None
    repository.assign_role_to_user(db, user=ctx["user"], role=role)
    db.commit()
    ctx["task"](project=ctx["projects"][2], due=TODAY)
    assert client.get("/home/projects").json()["total"] == 2
    assert client.get("/home/tasks").json()["summary"]["total"] == 0
    assert client.get("/projects").json()["total"] == 2
    assert client.get("/projects?member_only=true").json()["total"] == 2


@pytest.mark.parametrize("path", ["/home/tasks", "/home/projects"])
def test_requires_authentication_and_valid_limits(client, create_test_user, path):
    """未認証や過大な取得要求を拒否する。"""
    assert client.get(path).status_code == 401
    authorize_as(client, create_test_user())
    for query in (
        "limit=0",
        "limit=11",
        "timezone=Invalid/Timezone",
        "timezone=/etc/passwd",
    ):
        assert client.get(f"{path}?{query}").status_code == 422
    assert client.get(path).json()["items"] == []


def test_calendar_day_uses_requested_timezone_at_utc_boundary():
    """UTCの日付跨ぎ、負のoffsetとDSTでも暦日を揃える。"""
    now = datetime(2026, 9, 28, 16, 30, tzinfo=UTC)
    assert home_today("Asia/Tokyo", now) == date(2026, 9, 29)
    assert home_today("America/Los_Angeles", now) == date(2026, 9, 28)
    assert home_today(
        "America/New_York", datetime(2026, 11, 1, 4, 30, tzinfo=UTC)
    ) == date(2026, 11, 1)


def test_membership_without_project_read_is_not_a_visible_project(
    client, db, home_context
):
    """所属だけではHOMEや日常用の案件一覧を閲覧できない。"""
    role = Role(key="home_without_read", name="閲覧なし", scope="project")
    db.add(role)
    db.flush()
    for member in home_context["members"]:
        member.role_id = role.id
    db.commit()
    assert client.get("/home/projects").json()["total"] == 0
    assert client.get("/projects?member_only=true").json()["total"] == 0


def test_many_tasks_and_projects_are_bounded_without_per_project_queries(
    client, db, home_context, create_test_project, assign_project_role
):
    """大量データでも返却行数を制限し、案件数によるN+1を防ぐ。"""
    from sqlalchemy import event

    from app.services.home import HomeService

    ctx = home_context
    for index in range(20):
        project = create_test_project(name=f"追加案件{index}")
        assign_project_role(user=ctx["user"], project=project, role_key="member")
    db.add_all(
        [
            Task(
                project_id=ctx["projects"][0].id,
                assignee_id=ctx["user"].id,
                task_code=f"MANY-{index}",
                title="大量タスク",
                status="todo",
                due_date=TODAY,
            )
            for index in range(350)
        ]
    )
    db.commit()
    response = client.get("/home/tasks").json()
    assert response["summary"]["total"] == 350
    assert len(response["items"]) == 8

    def measure(limit):
        statements = []

        def record(*args):
            statements.append(args[2])

        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            result = HomeService().projects(db, ctx["user"], "Asia/Tokyo", limit)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert result.total == 22
        assert len(result.items) == limit
        return len(statements)

    db.refresh(ctx["user"])
    small_count, large_count = measure(1), measure(10)
    assert small_count == large_count
    assert large_count <= 6
