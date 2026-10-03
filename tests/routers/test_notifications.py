"""通知の差分判定、宛先認可、履歴の再現、トランザクションを検証する。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models.notification import Notification, NotificationTargetType
from app.models.project import ProjectMember
from app.models.requirement import RequirementDocument
from app.models.task import Task
from app.repositories.notification import NotificationRepository
from app.services.notification import NotificationService
from tests.helpers.auth import authorize_as
from tests.routers.test_comment_mentions import (
    detail_url,
    occurrence,
)
from tests.routers.test_comment_mentions import (
    mention_context as mention_context,
)


@pytest.fixture(params=["task", "requirement", "open_issue"])
def assignment_context(
    request,
    client,
    create_test_user,
    create_test_project,
    assign_project_role,
    create_test_requirement_document,
):
    """担当者を持つ主要3機能を同じ操作で検証する。"""
    actor = create_test_user(email="actor@example.com", name="操作ユーザー")
    first = create_test_user(email="first@example.com", name="担当A")
    second = create_test_user(email="second@example.com", name="担当B")
    project = create_test_project()
    for user in (actor, first, second):
        assign_project_role(user=user, project=project, role_key="project_admin")
    authorize_as(client, actor)
    base = f"/projects/{project.id}"
    kind = request.param
    data = {"title": "通知対象"}
    if kind == "task":
        url = f"{base}/tasks"
        field = "assignee_id"
    else:
        document = create_test_requirement_document(project=project)
        data["document_id"] = document.id
        url = f"{base}/{'requirements' if kind == 'requirement' else 'open-issues'}"
        field = "owner_id" if kind == "requirement" else "assignee_id"
        if kind == "requirement":
            data["requirement_type"] = "functional"
    return dict(
        actor=actor,
        first=first,
        second=second,
        project=project,
        kind=kind,
        url=url,
        field=field,
        data=data,
    )


def resource_url(ctx, identifier):
    """タスクだけが案件外の更新URLを使用する。"""
    return (
        f"/tasks/{identifier}"
        if ctx["kind"] == "task"
        else f"{ctx['url']}/{identifier}"
    )


def rows(db):
    """別APIセッションで作成した通知を現在の状態で読む。"""
    db.expire_all()
    return list(db.scalars(select(Notification).order_by(Notification.id)))


def test_assignment_transitions(client, db, assignment_context):
    """作成、A→A、別項目、A→B、自己割当、解除を検証する。"""
    ctx = assignment_context
    created = client.post(
        ctx["url"], json={**ctx["data"], ctx["field"]: ctx["first"].id}
    )
    assert created.status_code == 201, created.text
    resource = created.json()
    assert [row.recipient_user_id for row in rows(db)] == [ctx["first"].id]
    endpoint = resource_url(ctx, resource["id"])
    for payload, expected in [
        ({ctx["field"]: ctx["first"].id}, 1),
        ({"description": "別のフィールドだけ更新"}, 1),
        ({ctx["field"]: ctx["second"].id}, 2),
        ({ctx["field"]: ctx["actor"].id}, 2),
        ({ctx["field"]: None}, 2),
    ]:
        changed = client.patch(
            endpoint, json={"version": resource["version"], **payload}
        )
        assert changed.status_code == 200, changed.text
        resource = changed.json()
        assert len(rows(db)) == expected
    assert [row.recipient_user_id for row in rows(db)] == [
        ctx["first"].id,
        ctx["second"].id,
    ]
    stale = client.patch(endpoint, json={"version": 1, ctx["field"]: ctx["second"].id})
    assert stale.status_code == 409
    assert len(rows(db)) == 2


def test_self_assignment_on_create(client, db, assignment_context):
    """新規作成時に自分を割り当てても通知しない。"""
    ctx = assignment_context
    response = client.post(
        ctx["url"], json={**ctx["data"], ctx["field"]: ctx["actor"].id}
    )
    assert response.status_code == 201, response.text
    assert rows(db) == []


def test_mention_differences_and_historical_deletion(client, db, mention_context):
    """同じ宛先は1件、自己通知なし、追加分だけ通知、削除後も残す。"""
    ctx = mention_context
    first, second, author = ctx["first"], ctx["second"], ctx["author"]
    token = f"@{first.name}"
    body = f"{token} {token} @{author.name}"
    response = client.post(
        ctx["url"],
        json={
            **ctx["extra"],
            ctx["field"]: body,
            "mentions": [
                occurrence(first),
                occurrence(first, len(token) + 1),
                occurrence(author, len(token) * 2 + 2),
            ],
        },
    )
    assert response.status_code == 201, response.text
    comment = response.json()
    assert [row.recipient_user_id for row in rows(db)] == [first.id]
    if ctx["kind"] != "requirement":
        body = f"{token} @{second.name}"
        response = client.patch(
            detail_url(ctx, comment["id"]),
            json={
                "version": comment["version"],
                "body": body,
                "mentions": [occurrence(first), occurrence(second, len(token) + 1)],
            },
        )
        assert response.status_code == 200, response.text
        comment = response.json()
        assert [row.recipient_user_id for row in rows(db)] == [first.id, second.id]
        response = client.patch(
            detail_url(ctx, comment["id"]),
            json={
                "version": comment["version"],
                "body": "メンションをすべて削除",
                "mentions": [],
            },
        )
        assert response.status_code == 200, response.text
        comment = response.json()
        assert len(rows(db)) == 2
        # 削除してから再度追加されたメンションは別バージョンのイベント。
        response = client.patch(
            detail_url(ctx, comment["id"]),
            json={
                "version": comment["version"],
                "body": token,
                "mentions": [occurrence(first)],
            },
        )
        assert response.status_code == 200, response.text
        comment = response.json()
        assert len(rows(db)) == 3
    count = len(rows(db))
    response = client.delete(
        detail_url(ctx, comment["id"]), params={"version": comment.get("version", 1)}
    )
    assert response.status_code in (200, 204), response.text
    assert len(rows(db)) == count
    authorize_as(client, first)
    listed = client.get("/notifications")
    assert listed.status_code == 200, listed.text
    assert all(item["target_status"] == "deleted" for item in listed.json()["items"])


def test_self_mention(client, db, mention_context):
    """自己メンションは全コメント種類で通知しない。"""
    ctx = mention_context
    author = ctx["author"]
    response = client.post(
        ctx["url"],
        json={
            **ctx["extra"],
            ctx["field"]: f"@{author.name}",
            "mentions": [occurrence(author)],
        },
    )
    assert response.status_code == 201, response.text
    assert rows(db) == []


def test_mention_update_same_recipient(client, db, mention_context):
    """本文や出現位置の編集では既存の宛先へ再通知しない。"""
    ctx = mention_context
    if ctx["kind"] == "requirement":
        pytest.skip("従来の要件コメントには編集APIがない")
    first = ctx["first"]
    response = client.post(
        ctx["url"],
        json={
            **ctx["extra"],
            "body": f"@{first.name}",
            "mentions": [occurrence(first)],
        },
    )
    assert response.status_code == 201
    comment = response.json()
    response = client.patch(
        detail_url(ctx, comment["id"]),
        json={
            "version": comment["version"],
            "body": f"確認 @{first.name}",
            "mentions": [occurrence(first, 3)],
        },
    )
    assert response.status_code == 200, response.text
    assert len(rows(db)) == 1


def test_api_ownership_read_filters_pagination(client, db, assignment_context):
    """ページ境界、本人限定、個別・案件別・全件既読、未読件数を検証する。"""
    ctx = assignment_context
    for index in range(5):
        response = client.post(
            ctx["url"],
            json={
                **ctx["data"],
                "title": f"通知{index}",
                ctx["field"]: ctx["first"].id,
            },
        )
        assert response.status_code == 201, response.text
    notification_id = rows(db)[0].id
    # 管理者でも他人の通知を取得・既読化できない。
    assert client.get("/notifications").json()["total"] == 0
    assert client.post(f"/notifications/{notification_id}/read").status_code == 404
    assert client.post("/notifications/read-all").json()["updated_count"] == 0
    authorize_as(client, ctx["first"])
    pages = [
        client.get("/notifications", params={"page": page, "page_size": 2}).json()
        for page in (1, 2, 3)
    ]
    identifiers = [item["id"] for page in pages for item in page["items"]]
    assert len(identifiers) == len(set(identifiers)) == 5
    assert identifiers == sorted(identifiers, reverse=True)
    assert pages[0]["total"] == 5
    assert (
        client.get("/notifications", params={"project_id": 99999}).json()["items"] == []
    )
    assert client.get("/notifications/unread-count").json()["count"] == 5
    read = client.post(f"/notifications/{notification_id}/read")
    assert read.status_code == 200, read.text
    assert read.json()["is_read"] is True
    assert read.json()["read_at"] is not None
    assert (
        client.post(f"/notifications/{notification_id}/read").json()["read_at"]
        == read.json()["read_at"]
    )
    assert (
        client.get("/notifications", params={"unread_only": True}).json()["total"] == 4
    )
    assert client.get("/notifications/unread-count").json()["count"] == 4
    assert (
        client.post("/notifications/read-all", params={"project_id": 99999}).json()[
            "updated_count"
        ]
        == 0
    )
    assert (
        client.post(
            "/notifications/read-all", params={"project_id": ctx["project"].id}
        ).json()["updated_count"]
        == 4
    )
    assert client.post("/notifications/read-all").json()["updated_count"] == 0
    assert client.get("/notifications/unread-count").json()["count"] == 0


def test_authentication_and_csrf(client):
    """未認証の通知操作は拒否される。"""
    assert client.get("/notifications").status_code == 401
    assert client.get("/notifications/unread-count").status_code == 401
    assert client.post("/notifications/1/read").status_code == 401


def test_snapshot_and_lost_access(client, db, assignment_context):
    """改名後の再現、所属喪失、対象または親の削除に耐える。"""
    ctx = assignment_context
    response = client.post(
        ctx["url"], json={**ctx["data"], ctx["field"]: ctx["first"].id}
    )
    assert response.status_code == 201, response.text
    ctx["actor"].name = "変更後の名前"
    ctx["project"].name = "変更後の案件名"
    db.commit()
    authorize_as(client, ctx["first"])
    item = client.get("/notifications").json()["items"][0]
    assert item["snapshot"]["actor_name"] == "操作ユーザー"
    assert item["snapshot"]["project_name"] == "Project Name"
    assert item["target_status"] == "available"
    membership = db.scalar(
        select(ProjectMember).where(ProjectMember.user_id == ctx["first"].id)
    )
    membership.deleted_at = datetime.now(UTC)
    db.commit()
    item = client.get("/notifications").json()["items"][0]
    assert item["target_status"] == "forbidden"
    if ctx["kind"] == "task":
        resource = db.get(Task, response.json()["id"])
    else:
        resource = db.get(RequirementDocument, response.json()["document_id"])
    resource.deleted_at = datetime.now(UTC)
    db.commit()
    assert client.get("/notifications").json()["items"][0]["target_status"] == "deleted"


def test_idempotency_and_return_assignment(client, db, assignment_context):
    """同じイベントの再評価は1件、再割当は別イベントになる。"""
    ctx = assignment_context
    response = client.post(
        ctx["url"], json={**ctx["data"], ctx["field"]: ctx["first"].id}
    )
    resource = response.json()
    notification = rows(db)[0]
    for _ in range(2):
        NotificationService().assignment_changed(
            db,
            project_id=ctx["project"].id,
            actor_id=ctx["actor"].id,
            target_type=NotificationTargetType(ctx["kind"]),
            target_id=resource["id"],
            version=resource["version"],
            previous_user_id=None,
            recipient_id=ctx["first"].id,
            title=resource["title"],
            document_id=notification.context.get("document_id"),
        )
    db.commit()
    assert len(rows(db)) == 1
    endpoint = resource_url(ctx, resource["id"])
    response = client.patch(
        endpoint, json={"version": resource["version"], ctx["field"]: ctx["second"].id}
    )
    resource = response.json()
    response = client.patch(
        endpoint, json={"version": resource["version"], ctx["field"]: ctx["first"].id}
    )
    assert response.status_code == 200, response.text
    assert len(rows(db)) == 3


def test_assignment_failure_rolls_back_domain(
    client, db, assignment_context, monkeypatch
):
    """通知保存の失敗では元の業務リソースも保存しない。"""
    ctx = assignment_context

    def fail(*args, **kwargs):
        raise RuntimeError("notification failure")

    monkeypatch.setattr(NotificationRepository, "create_once", fail)
    with pytest.raises(RuntimeError, match="notification failure"):
        client.post(ctx["url"], json={**ctx["data"], ctx["field"]: ctx["first"].id})
    assert rows(db) == []
    params = (
        {"document_id": ctx["data"]["document_id"]}
        if "document_id" in ctx["data"]
        else {}
    )
    assert client.get(ctx["url"], params=params).json()["items"] == []


def test_mention_failure_rolls_back_domain(client, db, mention_context, monkeypatch):
    """通知失敗時にはコメントとメンション関連を残さない。"""
    ctx = mention_context

    def fail(*args, **kwargs):
        raise RuntimeError("notification failure")

    monkeypatch.setattr(NotificationRepository, "create_once", fail)
    with pytest.raises(RuntimeError, match="notification failure"):
        client.post(
            ctx["url"],
            json={
                **ctx["extra"],
                ctx["field"]: f"@{ctx['first'].name}",
                "mentions": [occurrence(ctx["first"])],
            },
        )
    assert rows(db) == []
    assert (
        client.get(
            ctx["url"], params=ctx["extra"] if ctx["kind"] == "target" else {}
        ).json()
        == []
    )


def test_assignment_update_failure_rolls_back(
    client, db, assignment_context, monkeypatch
):
    """失敗した担当変更は前の担当者とバージョンを維持する。"""
    ctx = assignment_context
    created = client.post(
        ctx["url"], json={**ctx["data"], ctx["field"]: ctx["first"].id}
    ).json()

    def fail(*args, **kwargs):
        raise RuntimeError("notification failure")

    monkeypatch.setattr(NotificationRepository, "create_once", fail)
    endpoint = resource_url(ctx, created["id"])
    with pytest.raises(RuntimeError, match="notification failure"):
        client.patch(
            endpoint,
            json={"version": created["version"], ctx["field"]: ctx["second"].id},
        )
    current = client.get(endpoint).json()
    assert current[ctx["field"]] == ctx["first"].id
    assert current["version"] == created["version"]
    assert len(rows(db)) == 1


def test_concurrent_assignments_use_one_version(client, db, assignment_context):
    """同じversionの同時割当では一つだけ保存・通知される。"""
    ctx = assignment_context
    created = client.post(ctx["url"], json=ctx["data"]).json()
    endpoint = resource_url(ctx, created["id"])

    def change(user):
        return client.patch(
            endpoint, json={"version": created["version"], ctx["field"]: user.id}
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(change, [ctx["first"], ctx["second"]]))
    assert sorted(response.status_code for response in results) == [200, 409]
    winner = next(
        response.json()[ctx["field"]]
        for response in results
        if response.status_code == 200
    )
    assert [notification.recipient_user_id for notification in rows(db)] == [winner]


def test_project_scope_and_csrf(
    client,
    db,
    create_test_user,
    create_test_project,
    create_test_task,
    assign_project_role,
):
    """案件別既読は別案件を残し、Cookie更新APIではCSRFを要求する。"""
    actor = create_test_user(email="actor@example.com")
    recipient = create_test_user(email="recipient@example.com")
    projects = [create_test_project(), create_test_project()]
    for project in projects:
        assign_project_role(user=recipient, project=project, role_key="member")
        task = create_test_task(project=project)
        NotificationService().assignment_changed(
            db,
            project_id=project.id,
            actor_id=actor.id,
            target_type=NotificationTargetType.TASK,
            target_id=task.id,
            version=1,
            previous_user_id=None,
            recipient_id=recipient.id,
            title=task.title,
        )
        db.commit()
    authorize_as(client, recipient)
    assert client.get("/notifications").json()["total"] == 2
    assert (
        client.get("/notifications", params={"project_id": projects[0].id}).json()[
            "total"
        ]
        == 1
    )
    assert (
        client.post(
            "/notifications/read-all", params={"project_id": projects[0].id}
        ).json()["updated_count"]
        == 1
    )
    assert client.get("/notifications/unread-count").json()["count"] == 1
    assert (
        client.get(
            "/notifications/unread-count", params={"project_id": projects[1].id}
        ).json()["count"]
        == 1
    )
    client.headers["X-CSRF-Token"] = "invalid"
    assert client.post("/notifications/read-all").status_code == 403
    assert client.post(f"/notifications/{rows(db)[-1].id}/read").status_code == 403
    assert client.get("/notifications/unread-count").json()["count"] == 1
