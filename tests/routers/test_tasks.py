"""タスク管理APIのテスト。"""

from collections.abc import Callable
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.project import Project, ProjectMember
from app.models.requirement import Requirement, RequirementDocument
from app.models.task import Board, Task, TaskChangeLog, TaskDependency
from app.models.user import User
from tests.helpers.auth import authorize_as


def test_create_task_returns_task_for_project_member(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    db: Session,
) -> None:
    """task:create権限を持つメンバーがタスクを作成できることを確認する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(project_code="TASK", name="Task Project")
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/tasks",
        json={
            "task_code": "TASK-001",
            "title": "ログインAPI実装",
            "task_type": "backend",
            "priority": "high",
            "start_date": "2026-07-01",
            "due_date": "2026-07-10",
        },
    )

    assert response.status_code == 201
    assert response.json()["task_code"] == "TASK-001"
    assert response.json()["project_id"] == project.id
    assert response.json()["version"] == 1
    assert response.json()["is_overdue"] is False
    assert response.json()["is_blocked"] is False
    assert db.query(TaskChangeLog).filter_by(action="created").count() == 1
    audit_log = db.query(AuditLog).filter_by(event_type="task.created").one()
    assert audit_log.actor_user_id == user.id
    assert audit_log.project_id == project.id


def test_create_task_rejects_duplicate_task_code(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
) -> None:
    """同一プロジェクト内のtask_code重複を拒否する。"""
    user = create_test_user(email="manager@example.com")
    project = create_test_project(project_code="DUPTASK", name="Dup Task")
    assign_project_role(user=user, project=project, role_key="manager")
    create_test_task(project=project, task_code="TASK-001")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/tasks",
        json={"task_code": "TASK-001", "title": "Duplicate"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "message": "Task code already exists",
        "code": "DUPLICATE_RESOURCE",
    }


def test_create_task_generates_task_code_when_omitted(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """task_code未指定時にプロジェクト単位で自動採番されることを確認する。"""
    user = create_test_user(email="auto-code@example.com")
    project = create_test_project(project_code="AUTOCODE", name="Auto Code")
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    first_response = client.post(
        f"/projects/{project.id}/tasks",
        json={"title": "First auto task"},
    )
    second_response = client.post(
        f"/projects/{project.id}/tasks",
        json={"task_code": None, "title": "Second auto task"},
    )
    blank_response = client.post(
        f"/projects/{project.id}/tasks",
        json={"task_code": "  ", "title": "Blank auto task"},
    )

    assert first_response.status_code == 201
    assert first_response.json()["task_code"] == "TASK-001"
    assert second_response.status_code == 201
    assert second_response.json()["task_code"] == "TASK-002"
    assert blank_response.status_code == 201
    assert blank_response.json()["task_code"] == "TASK-003"


def test_create_task_generates_task_code_per_project(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
) -> None:
    """自動採番がプロジェクト単位で独立していることを確認する。"""
    user = create_test_user(email="auto-code-project@example.com")
    first_project = create_test_project(project_code="AUTOP1", name="Auto Project 1")
    second_project = create_test_project(project_code="AUTOP2", name="Auto Project 2")
    create_test_task(project=first_project, task_code="TASK-009")
    assign_project_role(user=user, project=first_project, role_key="member")
    assign_project_role(user=user, project=second_project, role_key="member")
    authorize_as(client, user)

    first_response = client.post(
        f"/projects/{first_project.id}/tasks",
        json={"title": "Next first project task"},
    )
    second_response = client.post(
        f"/projects/{second_project.id}/tasks",
        json={"title": "First second project task"},
    )

    assert first_response.status_code == 201
    assert first_response.json()["task_code"] == "TASK-010"
    assert second_response.status_code == 201
    assert second_response.json()["task_code"] == "TASK-001"


def test_list_tasks_filters_by_requirement_and_overdue(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement: Callable[..., Requirement],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """タスク一覧がrequirement_idと期限超過で絞り込めることを確認する。"""
    user = create_test_user(email="viewer@example.com")
    project = create_test_project(project_code="FILTER", name="Filter")
    document = create_test_requirement_document(project=project)
    requirement = create_test_requirement(document=document)
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    create_response = client.post(
        f"/projects/{project.id}/tasks",
        json={
            "task_code": "TASK-OVERDUE",
            "title": "Overdue task",
            "due_date": (date.today() - timedelta(days=1)).isoformat(),
            "requirement_id": requirement.id,
        },
    )
    assert create_response.status_code == 201
    client.post(
        f"/projects/{project.id}/tasks",
        json={
            "task_code": "TASK-FUTURE",
            "title": "Future task",
            "due_date": (date.today() + timedelta(days=1)).isoformat(),
        },
    )

    response = client.get(
        f"/projects/{project.id}/tasks"
        f"?requirement_id={requirement.id}&overdue=true"
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["task_code"] == "TASK-OVERDUE"
    assert response.json()["items"][0]["is_overdue"] is True


def test_list_tasks_filters_by_parent_task_and_root_only(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
    db: Session,
) -> None:
    """タスク一覧を親タスクIDとルートタスクで絞り込めることを確認する。"""
    user = create_test_user(email="task-tree@example.com")
    project = create_test_project(project_code="TREE", name="Task Tree")
    parent = create_test_task(project=project, task_code="TASK-PARENT")
    child = create_test_task(project=project, task_code="TASK-CHILD")
    root = create_test_task(project=project, task_code="TASK-ROOT")
    child.parent_task_id = parent.id
    db.commit()
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    child_response = client.get(
        f"/projects/{project.id}/tasks?parent_task_id={parent.id}"
    )
    root_response = client.get(f"/projects/{project.id}/tasks?root_only=true")

    assert child_response.status_code == 200
    assert child_response.json()["total"] == 1
    assert child_response.json()["items"][0]["id"] == child.id
    assert root_response.status_code == 200
    assert {item["id"] for item in root_response.json()["items"]} == {
        parent.id,
        root.id,
    }


def test_list_tasks_rejects_conflicting_parent_filters(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
) -> None:
    """parent_task_idとroot_onlyを同時指定すると400になることを確認する。"""
    user = create_test_user(email="task-tree-conflict@example.com")
    project = create_test_project(project_code="TREECONF", name="Task Tree Conflict")
    parent = create_test_task(project=project, task_code="TASK-PARENT")
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    response = client.get(
        f"/projects/{project.id}/tasks?parent_task_id={parent.id}&root_only=true"
    )

    assert response.status_code == 400
    assert response.json()["message"] == (
        "parent_task_id and root_only cannot be used together"
    )


def test_list_tasks_rejects_parent_task_from_other_project(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
) -> None:
    """別プロジェクトの親タスクIDでの一覧絞り込みを404で拒否する。"""
    user = create_test_user(email="task-tree-other@example.com")
    project = create_test_project(project_code="TREEA", name="Task Tree A")
    other_project = create_test_project(project_code="TREEB", name="Task Tree B")
    other_parent = create_test_task(project=other_project, task_code="TASK-OTHER")
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    response = client.get(
        f"/projects/{project.id}/tasks?parent_task_id={other_parent.id}"
    )

    assert response.status_code == 404
    assert response.json()["message"] == "Parent task not found"


def test_update_task_uses_optimistic_lock(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
) -> None:
    """タスク更新が楽観的排他制御を行うことを確認する。"""
    user = create_test_user(email="manager@example.com")
    project = create_test_project(project_code="LOCK", name="Lock")
    task = create_test_task(project=project, task_code="TASK-LOCK")
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    response = client.patch(
        f"/tasks/{task.id}",
        json={"version": 999, "title": "Updated"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_CONFLICT"
    assert response.json()["current"]["id"] == task.id


def test_update_task_rejects_self_parent(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
) -> None:
    """タスク自身を親タスクに指定できないことを確認する。"""
    user = create_test_user(email="self-parent@example.com")
    project = create_test_project(project_code="SELFPARENT", name="Self Parent")
    task = create_test_task(project=project, task_code="TASK-SELF")
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    response = client.patch(
        f"/tasks/{task.id}",
        json={"version": task.version, "parent_task_id": task.id},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Task cannot be its own parent"


def test_update_task_rejects_parent_cycle(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
    db: Session,
) -> None:
    """親子関係が循環する更新を拒否することを確認する。"""
    user = create_test_user(email="parent-cycle@example.com")
    project = create_test_project(project_code="CYCLE", name="Parent Cycle")
    parent = create_test_task(project=project, task_code="TASK-PARENT")
    child = create_test_task(project=project, task_code="TASK-CHILD")
    grandchild = create_test_task(project=project, task_code="TASK-GRANDCHILD")
    child.parent_task_id = parent.id
    grandchild.parent_task_id = child.id
    db.commit()
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    response = client.patch(
        f"/tasks/{parent.id}",
        json={"version": parent.version, "parent_task_id": grandchild.id},
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Task parent creates a cycle"


def test_update_task_can_clear_parent_task(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
    db: Session,
) -> None:
    """parent_task_idにnullを送ると親タスクを解除できることを確認する。"""
    user = create_test_user(email="clear-parent@example.com")
    project = create_test_project(project_code="CLEARPARENT", name="Clear Parent")
    parent = create_test_task(project=project, task_code="TASK-PARENT")
    child = create_test_task(project=project, task_code="TASK-CHILD")
    child.parent_task_id = parent.id
    db.commit()
    db.refresh(child)
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    response = client.patch(
        f"/tasks/{child.id}",
        json={"version": child.version, "parent_task_id": None},
    )

    assert response.status_code == 200
    assert response.json()["parent_task_id"] is None


def test_dependency_marks_successor_as_blocked(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
) -> None:
    """未完了の先行タスクがある場合、後続タスクがblocked扱いになる。"""
    user = create_test_user(email="manager@example.com")
    project = create_test_project(project_code="DEP", name="Dependency")
    predecessor = create_test_task(project=project, task_code="TASK-PRE")
    successor = create_test_task(project=project, task_code="TASK-SUC")
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    response = client.post(
        f"/tasks/{successor.id}/dependencies",
        json={
            "predecessor_task_id": predecessor.id,
            "successor_task_id": successor.id,
            "dependency_type": "finish_to_start",
            "lag_days": 0,
        },
    )
    assert response.status_code == 201

    read_response = client.get(f"/tasks/{successor.id}")

    assert read_response.status_code == 200
    assert read_response.json()["is_blocked"] is True


def test_dependency_rejects_cycle(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
    db: Session,
) -> None:
    """循環依存を拒否する。"""
    user = create_test_user(email="manager@example.com")
    project = create_test_project(project_code="CYCLE", name="Cycle")
    first = create_test_task(project=project, task_code="TASK-1")
    second = create_test_task(project=project, task_code="TASK-2")
    db.add(
        TaskDependency(
            predecessor_task_id=first.id,
            successor_task_id=second.id,
            dependency_type="finish_to_start",
        )
    )
    db.commit()
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    response = client.post(
        f"/tasks/{first.id}/dependencies",
        json={
            "predecessor_task_id": second.id,
            "successor_task_id": first.id,
            "dependency_type": "finish_to_start",
            "lag_days": 0,
        },
    )

    assert response.status_code == 400
    assert response.json()["message"] == "Task dependency creates a cycle"


def test_move_task_updates_status_and_sort_order(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
    create_test_board: Callable[..., Board],
) -> None:
    """ボード移動APIがstatusとsort_orderを更新することを確認する。"""
    user = create_test_user(email="manager@example.com")
    project = create_test_project(project_code="BOARD", name="Board")
    task = create_test_task(project=project, task_code="TASK-MOVE")
    board = create_test_board(project=project)
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    response = client.post(
        f"/boards/{board.id}/tasks/{task.id}/move",
        json={"status": "in_progress", "sort_order": 3, "version": task.version},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"
    assert response.json()["sort_order"] == 3
    assert response.json()["actual_start_date"] == date.today().isoformat()


def test_gantt_returns_tasks_dependencies_and_milestones(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
) -> None:
    """簡易ガントAPIがタスク、依存関係、マイルストーンを返すことを確認する。"""
    user = create_test_user(email="manager@example.com")
    project = create_test_project(project_code="GANTT", name="Gantt")
    first = create_test_task(project=project, task_code="TASK-G1")
    second = create_test_task(project=project, task_code="TASK-G2")
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)
    client.post(
        f"/tasks/{second.id}/dependencies",
        json={
            "predecessor_task_id": first.id,
            "successor_task_id": second.id,
            "dependency_type": "finish_to_start",
            "lag_days": 0,
        },
    )
    client.post(
        f"/projects/{project.id}/milestones",
        json={
            "title": "MVP Release",
            "target_date": "2026-09-30",
            "status": "planned",
        },
    )

    response = client.get(f"/projects/{project.id}/gantt")

    assert response.status_code == 200
    assert len(response.json()["tasks"]) == 2
    assert len(response.json()["dependencies"]) == 1
    assert response.json()["milestones"][0]["title"] == "MVP Release"


def test_task_comments_lifecycle(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
) -> None:
    """タスクコメントの作成、更新、解決、再オープン、削除を確認する。"""
    user = create_test_user(email="commenter@example.com", name="Comment User")
    project = create_test_project(project_code="COMMENT", name="Comment")
    task = create_test_task(project=project, task_code="TASK-COMMENT")
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    create_response = client.post(
        f"/tasks/{task.id}/comments",
        json={"body": "確認してください"},
    )
    assert create_response.status_code == 201
    comment = create_response.json()
    assert comment["body"] == "確認してください"
    assert comment["is_resolved"] is False
    assert comment["version"] == 1
    assert comment["created_by_user"] == {
        "id": user.id,
        "name": "Comment User",
        "email": "commenter@example.com",
        "avatar_url": None,
    }

    list_response = client.get(f"/tasks/{task.id}/comments")
    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == comment["id"]
    assert list_response.json()[0]["created_by_user"]["name"] == "Comment User"

    update_response = client.patch(
        f"/task-comments/{comment['id']}",
        json={"version": comment["version"], "body": "修正しました"},
    )
    assert update_response.status_code == 200
    comment = update_response.json()
    assert comment["body"] == "修正しました"
    assert comment["version"] == 2

    resolve_response = client.post(
        f"/task-comments/{comment['id']}/resolve",
        json={"version": comment["version"]},
    )
    assert resolve_response.status_code == 200
    comment = resolve_response.json()
    assert comment["is_resolved"] is True

    reopen_response = client.post(
        f"/task-comments/{comment['id']}/reopen",
        json={"version": comment["version"]},
    )
    assert reopen_response.status_code == 200
    comment = reopen_response.json()
    assert comment["is_resolved"] is False

    delete_response = client.delete(f"/task-comments/{comment['id']}")
    assert delete_response.status_code == 204
    assert client.get(f"/tasks/{task.id}/comments").json() == []


def test_task_change_logs_include_comment_logs(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
) -> None:
    """タスク変更履歴APIがコメント履歴も安定コードで返すことを確認する。"""
    user = create_test_user(email="comment-history@example.com")
    project = create_test_project(project_code="TCLOG", name="Task Comment Log")
    task = create_test_task(project=project, task_code="TASK-CLOG")
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    create_response = client.post(
        f"/tasks/{task.id}/comments",
        json={"body": "確認してください"},
    )
    comment = create_response.json()
    update_response = client.patch(
        f"/task-comments/{comment['id']}",
        json={"version": comment["version"], "body": "確認しました"},
    )
    assert update_response.status_code == 200

    response = client.get(f"/tasks/{task.id}/change-logs")

    assert response.status_code == 200
    comment_logs = [
        item
        for item in response.json()["items"]
        if item["target_type"] == "task_comment"
    ]
    assert {log["action"] for log in comment_logs} >= {
        "comment_created",
        "comment_updated",
    }
    assert all(log["task_id"] == task.id for log in comment_logs)
    assert comment_logs[0]["created_by_user"]["email"] == "comment-history@example.com"


def test_viewer_cannot_create_task_comment(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
) -> None:
    """viewerはコメント作成権限を持たないことを確認する。"""
    user = create_test_user(email="task-viewer@example.com")
    project = create_test_project(project_code="TCVIEW", name="Task Comment Viewer")
    task = create_test_task(project=project, task_code="TASK-VIEW")
    assign_project_role(user=user, project=project, role_key="viewer")
    authorize_as(client, user)

    read_response = client.get(f"/tasks/{task.id}/comments")
    assert read_response.status_code == 200

    create_response = client.post(
        f"/tasks/{task.id}/comments",
        json={"body": "viewer comment"},
    )
    assert create_response.status_code == 403


def test_task_change_logs_return_field_values(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
) -> None:
    """タスク変更履歴APIがフィールド単位の値を返すことを確認する。"""
    user = create_test_user(email="history@example.com")
    project = create_test_project(project_code="HISTORY", name="History")
    task = create_test_task(project=project, task_code="TASK-HIST")
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    update_response = client.patch(
        f"/tasks/{task.id}",
        json={
            "version": task.version,
            "status": "in_progress",
            "change_reason": "着手したため",
        },
    )
    assert update_response.status_code == 200

    response = client.get(f"/tasks/{task.id}/change-logs")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    status_log = next(
        item for item in body["items"] if item["field_name"] == "status"
    )
    assert status_log["action"] == "status_changed"
    assert status_log["old_value"] == {"code": "backlog", "label": "バックログ"}
    assert status_log["new_value"] == {
        "code": "in_progress",
        "label": "作業中",
    }
    assert status_log["reason"] == "着手したため"
    assert status_log["created_by_user"]["email"] == "history@example.com"


def test_task_change_logs_create_single_dedicated_log_for_single_field_changes(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """単一の専用変更では専用actionだけが作成されることを確認する。"""
    user = create_test_user(email="single-change@example.com")
    project = create_test_project(project_code="SINGLELOG", name="Single Log")
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)
    cases = [
        ("status", "in_progress", "status_changed"),
        ("assignee_id", user.id, "assignee_changed"),
        ("due_date", "2026-07-10", "schedule_changed"),
        ("progress_percent", 50, "progress_changed"),
    ]

    for index, (field_name, value, expected_action) in enumerate(cases, start=1):
        create_response = client.post(
            f"/projects/{project.id}/tasks",
            json={"title": f"Single change task {index}"},
        )
        assert create_response.status_code == 201
        task = create_response.json()

        update_response = client.patch(
            f"/tasks/{task['id']}",
            json={"version": task["version"], field_name: value},
        )
        assert update_response.status_code == 200

        response = client.get(f"/tasks/{task['id']}/change-logs")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        update_logs = [
            item for item in body["items"] if item["action"] != "created"
        ]
        assert len(update_logs) == 1
        assert update_logs[0]["action"] == expected_action
        assert update_logs[0]["field_name"] == field_name


def test_task_change_logs_create_only_updated_log_for_multiple_field_changes(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
) -> None:
    """複数項目変更では汎用updatedだけが作成されることを確認する。"""
    user = create_test_user(email="multiple-change@example.com")
    project = create_test_project(project_code="MULTILOG", name="Multi Log")
    task = create_test_task(project=project, task_code="TASK-MULTI")
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    update_response = client.patch(
        f"/tasks/{task.id}",
        json={
            "version": task.version,
            "status": "in_progress",
            "assignee_id": user.id,
        },
    )
    assert update_response.status_code == 200

    response = client.get(f"/tasks/{task.id}/change-logs")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "updated"
    assert body["items"][0]["field_name"] is None
    assert set(body["items"][0]["new_value"]["updated_fields"]) == {
        "assignee_id",
        "status",
    }


def test_task_change_logs_handle_many_updated_fields(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
) -> None:
    """多数フィールド同時更新でも変更履歴登録が失敗しないことを確認する。"""
    user = create_test_user(email="many-fields@example.com")
    project = create_test_project(project_code="MANYLOG", name="Many Log")
    task = create_test_task(project=project, task_code="TASK-MANY")
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    update_response = client.patch(
        f"/tasks/{task.id}",
        json={
            "version": task.version,
            "title": "Updated title",
            "description": "Updated description",
            "task_type": "backend",
            "status": "todo",
            "priority": "high",
            "start_date": "2026-06-26",
            "due_date": "2028-01-03",
            "progress_percent": 0,
            "estimated_minutes": 120,
            "actual_minutes": 30,
            "sort_order": 2,
            "tags": ["backend", "history"],
        },
    )
    assert update_response.status_code == 200

    response = client.get(f"/tasks/{task.id}/change-logs")

    assert response.status_code == 200
    updated_log = next(
        item for item in response.json()["items"] if item["action"] == "updated"
    )
    assert updated_log["field_name"] is None
    assert "updated_fields" in updated_log["new_value"]
    assert "title" in updated_log["new_value"]["updated_fields"]
    assert "description" in updated_log["new_value"]["updated_fields"]
    assert "progress_percent" not in updated_log["new_value"]["updated_fields"]
    assert "snapshot" in updated_log["old_value"]
    assert "snapshot" in updated_log["new_value"]
    assert updated_log["old_value"]["snapshot"]["description"] is None
    assert updated_log["new_value"]["snapshot"]["description"] == (
        "Updated description"
    )
    for field_name in updated_log["new_value"]["updated_fields"]:
        assert field_name in updated_log["old_value"]["snapshot"]
        assert field_name in updated_log["new_value"]["snapshot"]


def test_task_update_does_not_record_change_log_when_values_are_unchanged(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
) -> None:
    """値が変わっていない更新では変更履歴を作成しないことを確認する。"""
    user = create_test_user(email="unchanged-task@example.com")
    project = create_test_project(project_code="UNCHANGED", name="Unchanged Task")
    task = create_test_task(
        project=project,
        task_code="TASK-UNCHANGED",
        title="Same title",
        status="todo",
        progress_percent=0,
    )
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    update_response = client.patch(
        f"/tasks/{task.id}",
        json={
            "version": task.version,
            "title": "Same title",
            "status": "todo",
            "progress_percent": 0,
        },
    )
    assert update_response.status_code == 200

    response = client.get(f"/tasks/{task.id}/change-logs")

    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_task_change_logs_normalize_legacy_comma_field_name(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
    db: Session,
) -> None:
    """古いカンマ区切りfield_nameの変更履歴でも500にならないことを確認する。"""
    user = create_test_user(email="legacy-history@example.com")
    project = create_test_project(project_code="LEGLOG", name="Legacy Log")
    task = create_test_task(project=project, task_code="TASK-LEGACY")
    assign_project_role(user=user, project=project, role_key="manager")
    db.add(
        TaskChangeLog(
            project_id=project.id,
            target_type="task",
            target_id=task.id,
            action="updated",
            field_name="due_date,start_date",
            old_value={"due_date": None, "start_date": None},
            new_value={"due_date": "2026-06-30", "start_date": "2026-06-24"},
            changed_by=user.id,
        )
    )
    db.commit()
    authorize_as(client, user)

    response = client.get(f"/tasks/{task.id}/change-logs")

    assert response.status_code == 200
    legacy_log = response.json()["items"][0]
    assert legacy_log["field_name"] is None
    assert legacy_log["old_value"] == {"due_date": None, "start_date": None}
    assert legacy_log["new_value"] == {
        "due_date": "2026-06-30",
        "start_date": "2026-06-24",
    }


def test_task_change_logs_return_parent_task_and_reporter_labels(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_task: Callable[..., Task],
    db: Session,
) -> None:
    """親タスクIDと報告者IDの変更履歴に表示補助が付くことを確認する。"""
    user = create_test_user(email="task-labels@example.com")
    reporter = create_test_user(
        email="task-reporter-label@example.com",
        name="Reporter User",
    )
    project = create_test_project(project_code="TASKLABEL", name="Task Label")
    parent_task = create_test_task(
        project=project,
        task_code="TASK-012",
        title="Parent task title",
    )
    task = create_test_task(project=project, task_code="TASK-013")
    assign_project_role(user=user, project=project, role_key="manager")
    db.add_all(
        [
            TaskChangeLog(
                project_id=project.id,
                target_type="task",
                target_id=task.id,
                action="updated",
                field_name="parent_task_id",
                old_value={"parent_task_id": None},
                new_value={"parent_task_id": parent_task.id},
                changed_by=user.id,
            ),
            TaskChangeLog(
                project_id=project.id,
                target_type="task",
                target_id=task.id,
                action="updated",
                field_name="reporter_id",
                old_value={"reporter_id": None},
                new_value={"reporter_id": reporter.id},
                changed_by=user.id,
            ),
        ]
    )
    db.commit()
    authorize_as(client, user)

    response = client.get(f"/tasks/{task.id}/change-logs")

    assert response.status_code == 200
    items = response.json()["items"]
    parent_log = next(item for item in items if item["field_name"] == "parent_task_id")
    reporter_log = next(item for item in items if item["field_name"] == "reporter_id")
    assert parent_log["new_value"] == {
        "id": parent_task.id,
        "label": "TASK-012 Parent task title",
    }
    assert reporter_log["new_value"] == {
        "id": reporter.id,
        "label": "Reporter User",
    }


def test_task_change_logs_return_requirement_labels(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement: Callable[..., Requirement],
    create_test_task: Callable[..., Task],
    db: Session,
) -> None:
    """要件ID配列の変更履歴に要件コードと表示ラベルが付くことを確認する。"""
    user = create_test_user(email="task-requirement-labels@example.com")
    project = create_test_project(project_code="TASKREQ", name="Task Requirement")
    document = create_test_requirement_document(project=project)
    requirement = create_test_requirement(
        document=document,
        requirement_code="REQ-001",
        title="Login requirement",
    )
    task = create_test_task(project=project, task_code="TASK-014")
    assign_project_role(user=user, project=project, role_key="manager")
    db.add(
        TaskChangeLog(
            project_id=project.id,
            target_type="task",
            target_id=task.id,
            action="updated",
            field_name="requirements",
            old_value={"requirements": []},
            new_value={"requirements": [{"id": requirement.id}]},
            changed_by=user.id,
        )
    )
    db.commit()
    authorize_as(client, user)

    response = client.get(f"/tasks/{task.id}/change-logs")

    assert response.status_code == 200
    requirement_log = response.json()["items"][0]
    assert requirement_log["field_name"] == "requirements"
    assert requirement_log["new_value"] == [
        {
            "id": requirement.id,
            "requirement_code": "REQ-001",
            "label": "REQ-001 Login requirement",
        }
    ]


def test_task_list_returns_requirements_and_filters_by_tags(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement: Callable[..., Requirement],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """タスク一覧が関連要件とタグ等の絞り込みを返すことを確認する。"""
    user = create_test_user(email="task-filter@example.com")
    project = create_test_project(project_code="TAGTASK", name="Tag Task")
    document = create_test_requirement_document(project=project)
    requirement = create_test_requirement(
        document=document,
        requirement_code="REQ-TAG",
        title="タグ検索要件",
    )
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    first_response = client.post(
        f"/projects/{project.id}/tasks",
        json={
            "task_code": "TASK-FE",
            "title": "Frontend task",
            "task_type": "frontend",
            "priority": "high",
            "due_date": "2026-07-02",
            "tags": ["frontend", "ui"],
            "requirement_id": requirement.id,
            "relation_type": "implements",
        },
    )
    assert first_response.status_code == 201
    second_response = client.post(
        f"/projects/{project.id}/tasks",
        json={
            "task_code": "TASK-BE",
            "title": "Backend task",
            "task_type": "backend",
            "priority": "medium",
            "due_date": "2026-07-01",
            "tags": ["backend"],
        },
    )
    assert second_response.status_code == 201

    response = client.get(
        f"/projects/{project.id}/tasks"
        "?task_type=frontend&priority=high&tag=frontend&sort=due_date_asc"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    task = body["items"][0]
    assert task["task_code"] == "TASK-FE"
    assert task["tags"] == ["frontend", "ui"]
    assert task["requirements"] == [
        {
            "id": requirement.id,
            "requirement_code": "REQ-TAG",
            "title": "タグ検索要件",
            "relation_id": task["requirements"][0]["relation_id"],
            "relation_type": "implements",
        }
    ]


def test_requirement_tasks_return_relation_id(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement: Callable[..., Requirement],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """要件配下タスク一覧にもrelation_idが含まれることを確認する。"""
    user = create_test_user(email="req-task@example.com")
    project = create_test_project(project_code="REQTASK", name="Requirement Task")
    document = create_test_requirement_document(project=project)
    requirement = create_test_requirement(document=document)
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)
    create_response = client.post(
        f"/requirements/{requirement.id}/tasks",
        json={
            "task_code": "TASK-REQ",
            "title": "Requirement task",
            "tags": ["requirement"],
        },
    )
    assert create_response.status_code == 201

    response = client.get(f"/requirements/{requirement.id}/tasks")

    assert response.status_code == 200
    requirement_summary = response.json()[0]["requirements"][0]
    assert requirement_summary["id"] == requirement.id
    assert requirement_summary["relation_id"] > 0
