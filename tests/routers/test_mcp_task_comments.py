"""開発用MCPの作業コメントの所属・再送・既存副作用を検証する。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.core.mcp import CLIENT_ID, PLUGIN_CLIENT_ID
from app.models.comment_mention import CommentMention
from app.models.mcp import McpConnection, McpOperationReceipt
from app.models.notification import Notification
from app.models.task import TaskChangeLog, TaskComment
from app.models.user import User
from app.repositories.mcp import McpRepository
from app.schemas.mcp_development import TaskCommentCreateInput, TaskCommentListInput
from app.schemas.mcp_tools import McpOperation
from app.services.mcp_operations import McpOperationsService
from tests.routers.test_mcp import context as context
from tests.routers.test_mcp import operate, remote_call
from tests.routers.test_mcp import remote_context as remote_context
from tests.routers.test_mcp_development import call
from tests.routers.test_mcp_development import development as development


@pytest.fixture
def task(development, create_test_task, db):
    """既存作業の説明と版を変更しないことを確認する。"""
    row = create_test_task(project=development[2])
    row.description = "保持する作業説明"
    db.commit()
    return row


def append(development, task, body="検証が完了しました。", **data):
    """通常の追記呼び出しは新しい再送キーを使う。"""
    return call(
        development,
        "create_task_comment",
        task_id=task.id,
        data={"body": body, **data},
    )


@pytest.mark.no_db
@pytest.mark.parametrize(
    "data",
    [
        {"body": ""},
        {"body": " \n"},
        {"body": "x" * 8001},
        {"body": "x\x00"},
        {"body": "x", "parent_comment_id": 0},
        {"body": "x", "status": "done"},
    ],
)
def test_input_rejects_empty_overlong_and_non_comment_updates(data):
    """入力境界と余計な業務更新をDBへ届く前に拒否する。"""
    with pytest.raises(ValidationError):
        TaskCommentCreateInput.model_validate(
            {
                "project_id": 1,
                "task_id": 1,
                "idempotency_key": "a" * 16,
                "data": data,
            }
        )
    with pytest.raises(ValidationError):
        TaskCommentListInput(project_id=1, task_id=1, page_size=51)


@pytest.mark.no_db
@pytest.mark.parametrize(
    "development_enabled,comments_enabled,client_id",
    [
        (False, True, CLIENT_ID),
        (True, False, CLIENT_ID),
        (True, True, PLUGIN_CLIENT_ID),
        (True, True, CLIENT_ID),
    ],
)
def test_comment_catalog_is_separately_opt_in(
    monkeypatch, development_enabled, comments_enabled, client_id
):
    """新flagだけで審査clientを拡張せず、OFFでも旧開発操作を保持する。"""
    monkeypatch.setattr(settings, "mcp_development_tools_enabled", development_enabled)
    monkeypatch.setattr(settings, "mcp_task_comments_enabled", comments_enabled)
    service = McpOperationsService()
    monkeypatch.setattr(service, "allowed", lambda *args: True)
    connection = McpConnection(project_ids=[1], client_id=client_id)
    enabled = development_enabled and comments_enabled and client_id == CLIENT_ID
    with Session() as db:
        names = {tool["name"] for tool in service.catalog(db, connection, User())}
        assert ("list_task_comments" in names) == enabled
        assert ("create_task_comment" in names) == enabled
        assert ("get_project_document" in names) == (
            development_enabled and client_id == CLIENT_ID
        )
        if not enabled:
            for name in ("list_task_comments", "create_task_comment"):
                with pytest.raises(NotFoundError):
                    service.execute(
                        db, connection, User(), McpOperation(tool=name, arguments={})
                    )


def test_page_replies_resolved_deleted_and_long_body(development, task, db):
    """新しい順のページ、返信/解決状態、旧長文の明示切詰めを保持する。"""
    parent = append(development, task, body="親コメント").json()
    assert "body" not in parent
    reply = append(
        development, task, body="返信コメント", parent_comment_id=parent["id"]
    ).json()
    db.get(TaskComment, parent["id"]).is_resolved = True
    legacy = TaskComment(
        task_id=task.id,
        body="長" * 8001,
        created_at=db.get(TaskComment, reply["id"]).created_at + timedelta(seconds=1),
    )
    deleted = TaskComment(
        task_id=task.id, body="削除済み", deleted_at=datetime.now(UTC)
    )
    db.add_all([legacy, deleted])
    db.commit()
    receipts = db.scalar(select(func.count()).select_from(McpOperationReceipt))
    pages = [
        call(
            development,
            "list_task_comments",
            key=False,
            task_id=task.id,
            page=index,
            page_size=1,
        ).json()
        for index in (1, 2, 3, 4)
    ]
    assert [p["total"] for p in pages] == [3] * 4
    assert pages[3]["items"] == []
    first = pages[0]["items"][0]
    assert first["body_truncated"] and first["body_length"] == 8001
    assert len(first["body"]) == 8000
    assert pages[1]["items"][0]["id"] == reply["id"]
    assert pages[1]["items"][0]["parent_comment_id"] == parent["id"]
    assert pages[2]["items"][0]["is_resolved"] is True
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(McpOperationReceipt)) == receipts
    assert task.description == "保持する作業説明" and task.version == 1
    assert db.scalar(select(func.count()).select_from(TaskChangeLog)) == 2


def test_mentions_replay_conflict_and_current_membership(
    development, task, db, create_test_user, assign_project_role
):
    """UTF-16メンションは一度だけ通知し、再送時も現在の所属を確認する。"""
    recipient = create_test_user(email="mention@example.com", name="確認者")
    membership = assign_project_role(
        user=recipient, project=development[2], role_key="member"
    )
    body = "😀 @確認者 完了"
    mention = {
        "user_id": recipient.id,
        "display_name": recipient.name,
        "start": 3,
        "end": 7,
    }
    args = {
        "task_id": task.id,
        "key": uuid4().hex,
        "data": {"body": body, "mentions": [mention]},
    }
    created = call(development, "create_task_comment", **args)
    assert created.status_code == 200, created.text
    assert call(development, "create_task_comment", **args).json() == created.json()
    assert (
        call(
            development, "create_task_comment", **{**args, "data": {"body": "別の入力"}}
        ).status_code
        == 409
    )
    fetched = call(development, "list_task_comments", key=False, task_id=task.id).json()
    assert fetched["items"][0]["body"] == body
    assert fetched["items"][0]["mentions"] == [mention]
    assert db.scalar(select(func.count()).select_from(CommentMention)) == 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.recipient_user_id == recipient.id)
        )
        == 1
    )
    db.delete(membership)
    db.commit()
    denied = call(development, "create_task_comment", **args)
    assert denied.status_code == 400 and "id" not in denied.json()


@pytest.mark.parametrize("removed", ["task", "parent", "comment"])
def test_deleted_targets_are_rechecked_before_receipt(development, task, db, removed):
    """対象/返信先/作成コメントが削除されれば古いreceiptのIDを返さない。"""
    parent = TaskComment(task_id=task.id, body="親")
    db.add(parent)
    db.commit()
    args = {
        "task_id": task.id,
        "key": uuid4().hex,
        "data": {"body": "返信", "parent_comment_id": parent.id},
    }
    created = call(development, "create_task_comment", **args)
    assert created.status_code == 200, created.text
    row = {
        "task": task,
        "parent": parent,
        "comment": db.get(TaskComment, created.json()["id"]),
    }[removed]
    row.deleted_at = datetime.now(UTC)
    db.commit()
    denied = call(development, "create_task_comment", **args)
    assert denied.status_code == 404 and "id" not in denied.json()


def test_project_boundaries_and_reply_target(
    development, task, db, create_test_project, create_test_task
):
    """未同意Project・別Projectの作業・別作業の返信先を混ぜない。"""
    other_project = create_test_project()
    foreign = create_test_task(project=other_project)
    same_project_task = create_test_task(project=development[2])
    parent = TaskComment(task_id=same_project_task.id, body="別作業の親")
    db.add(parent)
    db.commit()
    assert append(development, task, parent_comment_id=parent.id).status_code == 404
    for name in ("list_task_comments", "create_task_comment"):
        args = {"task_id": foreign.id}
        if name == "create_task_comment":
            args["data"] = {"body": "越境"}
        assert (
            call(
                development,
                name,
                key=False if name.startswith("list") else None,
                **args,
            ).status_code
            == 404
        )
        direct, headers, _ = development
        assert (
            operate(
                direct,
                headers,
                name,
                other_project.id,
                key=False if name.startswith("list") else None,
                **args,
            ).status_code
            == 403
        )


@pytest.mark.parametrize("permission", ["task:read", "task:comment"])
def test_current_permissions_apply_to_receipt_and_catalog(
    development, task, monkeypatch, permission
):
    """一度成功しても、現在の業務権限を剥奪すると再送を拒否する。"""
    args = {"task_id": task.id, "key": uuid4().hex, "data": {"body": "進捗"}}
    assert call(development, "create_task_comment", **args).status_code == 200
    from app.services.authorization import AuthorizationService

    original = AuthorizationService.has_project_permission

    def allowed(self, *args, **kwargs):
        return (
            False
            if kwargs.get("permission_code") == permission
            else original(self, *args, **kwargs)
        )

    monkeypatch.setattr(AuthorizationService, "has_project_permission", allowed)
    assert call(development, "create_task_comment", **args).status_code == 403
    direct, headers, _ = development
    catalog = direct.get("/integrations/mcp/catalog", headers=headers).json()["tools"]
    assert "create_task_comment" not in {row["name"] for row in catalog}
    read = call(development, "list_task_comments", key=False, task_id=task.id)
    assert read.status_code == (403 if permission == "task:read" else 200)


def test_flag_and_revoked_connection_block_direct_calls(
    development, task, db, monkeypatch
):
    """一覧を使わない直接呼出でもflagと接続失効を検証する。"""
    monkeypatch.setattr(settings, "mcp_development_tools_enabled", False)
    assert append(development, task).status_code == 404
    assert (
        call(development, "list_task_comments", key=False, task_id=task.id).status_code
        == 404
    )
    monkeypatch.setattr(settings, "mcp_development_tools_enabled", True)
    monkeypatch.setattr(settings, "mcp_task_comments_enabled", False)
    assert append(development, task).status_code == 404
    monkeypatch.setattr(settings, "mcp_task_comments_enabled", True)
    db.scalar(select(McpConnection)).revoked_at = datetime.now(UTC)
    db.commit()
    assert append(development, task).status_code == 401


def test_failed_receipt_rolls_back_comment_mentions_notification_and_history(
    development, task, db, monkeypatch, create_test_user, assign_project_role
):
    """内部commitがあってもMCPの失敗でコメントと副作用を残さない。"""
    recipient = create_test_user(email="notify@example.com", name="確認者")
    assign_project_role(user=recipient, project=development[2], role_key="member")

    def fail(*args, **kwargs):
        raise RuntimeError("receipt unavailable")

    monkeypatch.setattr(McpRepository, "save_receipt", fail)
    with pytest.raises(RuntimeError, match="receipt unavailable"):
        append(
            development,
            task,
            body="@確認者",
            mentions=[
                {
                    "user_id": recipient.id,
                    "display_name": recipient.name,
                    "start": 0,
                    "end": 4,
                }
            ],
        )
    db.expire_all()
    for model in (TaskComment, CommentMention, Notification, TaskChangeLog):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert task.description == "保持する作業説明" and task.version == 1


def test_comment_roundtrip_through_embedded_sdk(
    remote_context, context, create_test_task, monkeypatch
):
    """SDKでも新しい2操作と出力schemaを提示し、8000字の境界を保存できる。"""
    monkeypatch.setattr(settings, "mcp_development_tools_enabled", True)
    monkeypatch.setattr(settings, "mcp_task_comments_enabled", True)
    task = create_test_task(project=context[1])
    remote, _ = remote_context
    catalog = remote.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        },
    ).json()["result"]["tools"]
    names = {tool["name"] for tool in catalog if tool.get("outputSchema")}
    assert {"list_task_comments", "create_task_comment"} <= names
    created = remote_call(
        remote,
        "create_task_comment",
        {
            "project_id": context[1].id,
            "task_id": task.id,
            "idempotency_key": uuid4().hex,
            "data": {"body": "あ" * 8000},
        },
    )
    assert not created.get("isError"), created
    read = remote_call(
        remote,
        "list_task_comments",
        {
            "project_id": context[1].id,
            "task_id": task.id,
        },
    )
    row = read["structuredContent"]["items"][0]
    assert row["id"] == created["structuredContent"]["id"]
    assert len(row["body"]) == row["body_length"] == 8000
    assert row["body_truncated"] is False


def test_truncated_body_only_keeps_complete_utf16_mentions(
    development, client, task, create_test_user, assign_project_role
):
    """絵文字と切詰め境界のメンションを、壊れた位置として返さない。"""
    recipient = create_test_user(email="long-mention@example.com", name="確認者")
    assign_project_role(user=recipient, project=development[2], role_key="member")
    prefix = "😀 @確認者 " + "a" * 7991
    body = prefix + "@確認者 後続"
    late_start = len(prefix.encode("utf-16-le")) // 2
    mentions = [
        {"user_id": recipient.id, "display_name": recipient.name, "start": 3, "end": 7},
        {
            "user_id": recipient.id,
            "display_name": recipient.name,
            "start": late_start,
            "end": late_start + 4,
        },
    ]
    created = client.post(
        f"/tasks/{task.id}/comments", json={"body": body, "mentions": mentions}
    )
    assert created.status_code == 201, created.text
    result = call(development, "list_task_comments", key=False, task_id=task.id)
    assert result.status_code == 200, result.text
    row = result.json()["items"][0]
    assert row["body_length"] == len(body) and row["body_truncated"] is True
    assert row["mentions"] == [mentions[0]] and row["mentions_truncated"] is True
    assert len(row["body"]) == 8000
