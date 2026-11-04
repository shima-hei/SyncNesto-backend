"""SQL増加の回帰と、最適化後の現在権限・ブロック判定を確認する。"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from app.core.config import settings
from app.core.mcp import PLUGIN_CLIENT_ID
from app.db.read_cache import read_cache
from app.models.mcp import McpConnection
from app.models.task import Task, TaskDependency
from app.services.authorization import AuthorizationService
from app.services.mcp_operations import McpOperationsService
from tests.helpers.auth import authorize_as


@pytest.mark.parametrize("size", [1, 100])
def test_list_blocking_is_one_query_with_and_without_dependencies(
    client, db, create_test_user, create_test_project, assign_project_role, size
):
    """表示件数でSQLを増やさず、完了・削除・依存種別の意味を保持する。"""
    user = create_test_user()
    project = create_test_project()
    assign_project_role(user=user, project=project, role_key="member")
    tasks = [
        Task(project_id=project.id, task_code=f"ITEM-{i}", title=f"対象{i}")
        for i in range(size)
    ]
    predecessors = [
        Task(project_id=project.id, task_code="P1", title="未完了"),
        Task(project_id=project.id, task_code="P2", title="完了", status="done"),
        Task(
            project_id=project.id,
            task_code="P3",
            title="削除",
            deleted_at=datetime.now(UTC),
        ),
    ]
    db.add_all(tasks + predecessors)
    db.flush()
    for i, task in enumerate(tasks):
        if i % 5 == 4:
            continue
        db.add(
            TaskDependency(
                predecessor_task_id=predecessors[min(i % 5, 2)].id,
                successor_task_id=task.id,
                dependency_type="start_to_start" if i % 5 == 3 else "finish_to_start",
            )
        )
    db.commit()
    expected = {task.id for i, task in enumerate(tasks) if i % 5 == 0}
    authorize_as(client, user)
    queries = []

    def collect(conn, cursor, statement, parameters, context, executemany):
        if "from task_dependencies join tasks" in " ".join(statement.lower().split()):
            queries.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", collect)
    try:
        response = client.get(
            f"/projects/{project.id}/tasks", params={"q": "対象", "page_size": size}
        )
    finally:
        event.remove(engine, "before_cursor_execute", collect)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == size
    assert {row["id"] for row in items if row["is_blocked"]} == expected
    assert len(queries) == 1


def test_mcp_catalog_batches_current_project_permissions(
    db, monkeypatch, create_test_user, create_test_project, assign_project_role
):
    """1案件の全ツールを一度で確認し、次の一覧には権限剥奪を反映する。"""
    for flag in (
        "mcp_plugin_extended_tools_enabled",
        "mcp_read_context_enabled",
        "mcp_task_comments_enabled",
    ):
        monkeypatch.setattr(settings, flag, True)
    user = create_test_user()
    project = create_test_project()
    member = assign_project_role(user=user, project=project, role_key="member")
    db.info.update(tenant_id=project.tenant_id, tenant_user_id=user.id)
    service = McpOperationsService()
    calls = []
    repository = service.auth.authorization.repository
    original = repository.project_permission_codes

    def counted(*args, **kwargs):
        calls.append(kwargs["project_id"])
        return original(*args, **kwargs)

    monkeypatch.setattr(repository, "project_permission_codes", counted)
    connection = McpConnection(client_id=PLUGIN_CLIENT_ID, project_ids=[project.id])
    names = {row["name"] for row in service.catalog(db, connection, user)}
    assert {"get_task", "create_task", "list_projects"} <= names
    assert calls == [project.id]
    member.deleted_at = datetime.now(UTC)
    db.commit()
    calls.clear()
    assert {row["name"] for row in service.catalog(db, connection, user)} == {
        "list_projects"
    }
    assert calls == [project.id]


def test_read_authorization_discards_snapshot_after_flush_and_rollback(
    db, create_test_user, create_test_project, assign_project_role
):
    """共通キャッシュを誤って更新経路で使っても、flush後の剥奪を見落とさない。"""
    user = create_test_user()
    project = create_test_project()
    member = assign_project_role(user=user, project=project, role_key="member")
    db.info.update(tenant_id=project.tenant_id, tenant_user_id=user.id)
    service = AuthorizationService()

    def allowed():
        return service.has_project_permission(
            db, user=user, project_id=project.id, permission_code="task:read"
        )

    with read_cache(db):
        assert allowed()
        member.deleted_at = datetime.now(UTC)
        db.flush()
        assert not allowed()
        db.rollback()
        assert allowed()
