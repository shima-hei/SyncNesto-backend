"""種別管理と理由付き差し戻しの業務/API境界。"""

import pytest
from sqlalchemy import select

from app.models.notification import Notification
from app.models.task import TaskChangeLog
from tests.helpers.auth import authorize_as


@pytest.fixture
def context(client, create_test_user, create_test_project, assign_project_role):
    """通常の案件管理者と担当者を用意する。"""
    admin = create_test_user(email="type-admin@example.com")
    member = create_test_user(email="type-member@example.com")
    project = create_test_project()
    assign_project_role(user=admin, project=project, role_key="project_admin")
    assign_project_role(user=member, project=project, role_key="member")
    authorize_as(client, admin)
    return admin, member, project


def test_custom_type_rename_hide_and_task_history(client, context):
    """名前を変更/非表示にしても同じTaskとキーを表示し、再選択は拒否する。"""
    _, _, project = context
    path = f"/projects/{project.id}/task-types"
    created = client.post(path, json={"name": "顧客調整"})
    assert created.status_code == 201, created.text
    kind = created.json()
    task = client.post(
        f"/projects/{project.id}/tasks",
        json={"title": "確認", "task_type": kind["key"]},
    ).json()
    assert task["task_type_name"] == "顧客調整"
    saved = client.patch(
        f"{path}/{kind['key']}",
        json={"version": 1, "name": "顧客への確認", "is_active": False},
    )
    assert saved.status_code == 200, saved.text
    current = client.get(f"/tasks/{task['id']}").json()
    assert current["task_type"] == kind["key"]
    assert current["task_type_name"] == "顧客への確認"
    assert current["version"] == task["version"]
    assert (
        client.post(
            f"/projects/{project.id}/tasks",
            json={"title": "新規", "task_type": kind["key"]},
        ).status_code
        == 400
    )
    assert (
        client.patch(
            f"/tasks/{task['id']}",
            json={
                "version": task["version"],
                "title": "編集",
                "task_type": kind["key"],
            },
        ).status_code
        == 200
    )
    assert any(
        row["key"] == kind["key"] and not row["is_active"]
        for row in client.get(path).json()["items"]
    )


@pytest.mark.parametrize("role", ["manager", "member", "viewer"])
def test_type_management_uses_existing_project_permission(
    client, context, create_test_user, assign_project_role, role
):
    """閲覧やタスク編集だけでは種別を管理できない。"""
    _, _, project = context
    user = create_test_user()
    assign_project_role(user=user, project=project, role_key=role)
    authorize_as(client, user)
    path = f"/projects/{project.id}/task-types"
    assert client.get(path).status_code == 200
    assert client.post(path, json={"name": "禁止"}).status_code == 403
    introduced = client.post(
        f"/projects/{project.id}/tasks",
        json={"title": "未登録の種別を持ち込まない", "task_type": "unregistered-kind"},
    )
    assert introduced.status_code == (403 if role == "viewer" else 400)


def test_type_conflicts_duplicates_foreign_key_and_legacy(
    client, context, create_test_project, assign_project_role, create_test_task, db
):
    """版/重複/案件境界を守り、既存の自由文字列は維持する。"""
    admin, _, project = context
    path = f"/projects/{project.id}/task-types"
    kind = client.post(path, json={"name": "Coordination"}).json()
    for name in ["coordination", "タスク（一般）"]:
        assert client.post(path, json={"name": name}).status_code == 409
    assert client.post(path, json={"name": "   "}).status_code == 422
    conflict = client.patch(
        f"{path}/{kind['key']}",
        json={"version": 99, "name": "上書きしない", "is_active": True},
    )
    assert conflict.status_code == 409
    assert conflict.json()["current"]["name"] == "Coordination"
    other = create_test_project()
    assign_project_role(user=admin, project=other, role_key="project_admin")
    assert (
        client.post(
            f"/projects/{other.id}/tasks",
            json={"title": "他案件", "task_type": kind["key"]},
        ).status_code
        == 400
    )
    assert (
        client.patch(
            f"/projects/{other.id}/task-types/{kind['key']}",
            json={"version": 1, "name": "他案件", "is_active": True},
        ).status_code
        == 404
    )
    legacy = create_test_task(project=project)
    legacy.task_type = "custom_old-value"
    db.commit()
    response = client.post(
        f"/projects/{project.id}/tasks",
        json={"title": "旧種別", "task_type": legacy.task_type},
    )
    assert response.status_code == 201, response.text
    assert response.json()["task_type_name"] == legacy.task_type


def test_return_requires_reason_preserves_fields_and_notifies_once(
    client, context, create_test_task, db
):
    """差戻しの理由/担当/実績/版/履歴/宛先を一つの保存で確認する。"""
    admin, member, project = context
    task = create_test_task(
        project=project,
        status="in_review",
        progress_percent=70,
    )
    task.assignee_id = member.id
    task.estimated_minutes = 120
    db.commit()
    before = client.get(f"/tasks/{task.id}").json()
    url = f"/tasks/{task.id}"
    assert (
        client.patch(
            url, json={"version": task.version, "status": "returned"}
        ).status_code
        == 400
    )
    saved = client.patch(
        url,
        json={
            "version": task.version,
            "status": "returned",
            "change_reason": "境界値を追加してください",
        },
    )
    assert saved.status_code == 200, saved.text
    current = saved.json()
    for field in [
        "assignee_id",
        "due_date",
        "progress_percent",
        "estimated_minutes",
        "actual_start_date",
        "actual_end_date",
        "actual_minutes",
    ]:
        assert current[field] == before[field]
    assert current["status"] == "returned"
    assert (
        db.scalar(
            select(TaskChangeLog).where(
                TaskChangeLog.target_id == task.id,
                TaskChangeLog.reason == "境界値を追加してください",
            )
        )
        is not None
    )
    rows = list(
        db.scalars(
            select(Notification).where(
                Notification.type == "task_returned",
                Notification.target_id == str(task.id),
            )
        )
    )
    assert len(rows) == 1 and rows[0].recipient_user_id == member.id
    assert rows[0].actor_user_id == admin.id
    repeated = client.patch(
        url, json={"version": current["version"], "status": "returned"}
    )
    assert repeated.status_code == 200
    assert (
        len(
            list(
                db.scalars(
                    select(Notification).where(
                        Notification.type == "task_returned",
                        Notification.target_id == str(task.id),
                    )
                )
            )
        )
        == 1
    )
    for status in ["in_progress", "in_review", "done"]:
        data = {
            "version": current["version"] + (1 if status == "in_progress" else 0),
            "status": status,
        }
        latest = client.get(url).json()
        data["version"] = latest["version"]
        if status == "done":
            data["progress_percent"] = 100
        assert client.patch(url, json=data).status_code == 200


def test_board_return_uses_same_reason_and_notification_contract(
    client, context, create_test_task, create_test_board, db
):
    """ボード経由でも理由を省略できず、同じ履歴/通知を保存する。"""
    _, member, project = context
    task = create_test_task(project=project, status="in_review", progress_percent=60)
    task.assignee_id = member.id
    db.commit()
    board = create_test_board(project=project)
    url = f"/boards/{board.id}/tasks/{task.id}/move"
    data = {"version": task.version, "status": "returned", "sort_order": 20}
    assert client.post(url, json=data).status_code == 400
    data["change_reason"] = "テストを追加"
    saved = client.post(url, json=data)
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "returned"
    assert saved.json()["progress_percent"] == 60
    assert saved.json()["actual_start_date"] is None
    assert saved.json()["sort_order"] == 20
    authorize_as(client, member)
    items = client.get("/notifications").json()["items"]
    assert any(
        row["type"] == "task_returned" and row["snapshot"]["excerpt"] == "テストを追加"
        for row in items
    )


def test_return_rejects_viewer_and_nonmember(
    client, context, create_test_task, create_test_user, assign_project_role
):
    """状態の追加で更新権限を広げない。"""
    _, _, project = context
    task = create_test_task(project=project)
    viewer = create_test_user(email="return-viewer@example.com")
    assign_project_role(user=viewer, project=project, role_key="viewer")
    for user in [viewer, create_test_user(email="return-outsider@example.com")]:
        authorize_as(client, user)
        assert client.patch(
            f"/tasks/{task.id}",
            json={
                "version": task.version,
                "status": "returned",
                "change_reason": "禁止",
            },
        ).status_code in {403, 404}
