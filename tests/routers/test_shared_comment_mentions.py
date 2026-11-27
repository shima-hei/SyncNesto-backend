"""固定版のメンション、通知とリンク先の認可を実PostgreSQLで検証する。"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import update

from app.models.project import ProjectMember
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_publications import create, publication_base
from tests.routers.test_artifact_releases import context as context  # noqa: F401
from tests.routers.test_publication_access import (
    access_fixture as access_fixture,
)
from tests.routers.test_publication_access import publish, shared_path


def test_staff_shared_notifications_follow_public_authors_and_permissions(
    client, db, access_fixture
):
    ctx = access_fixture
    (guest, participation), (other, _) = ctx["guests"]
    staff = ctx["user"]
    share, _, _ = publish(
        client,
        ctx,
        recipient_options={participation.id: {"comment_allowed": True}},
    )
    shared = shared_path(ctx, "/comments")
    internal = f"{publication_base(ctx)}/{share['id']}/comments"
    candidates = shared + "/mention-candidates"
    authorize_as(client, guest)
    assert client.get(candidates).json() == []
    authorize_as(client, other)
    assert client.get(candidates).status_code == 404

    authorize_as(client, staff)
    root = client.post(
        internal, json={"request_id": str(uuid4()), "body": "共有の窓口です"}
    )
    assert root.status_code == 201, root.text
    thread = root.json()["id"]
    public_label = root.json()["author_label"]
    authorize_as(client, guest)
    response = client.get(candidates, params={"q": public_label})
    assert response.status_code == 200, response.text
    (candidate,) = response.json()
    assert set(candidate) == {"id", "name"}
    assert candidate["name"] == public_label
    assert candidate["id"] != thread
    body = "@" + public_label
    data = {
        "request_id": str(uuid4()),
        "parent_id": thread,
        "body": body,
        "mentions": [
            {
                "recipient_id": candidate["id"],
                "display_name": public_label,
                "start": 0,
                "end": len(body.encode("utf-16-le")) // 2,
            }
        ],
    }
    reply = client.post(shared, json=data)
    assert reply.status_code == 201, reply.text
    assert client.post(shared, json=data).json() == reply.json()
    assert candidate["id"] not in reply.text
    ordinary = client.post(
        shared,
        json={"request_id": str(uuid4()), "parent_id": thread, "body": "受領しました"},
    )
    assert ordinary.status_code == 201, ordinary.text
    assert not [
        item
        for item in client.get("/shared/activity").json()["items"]
        if item["id"] in {reply.json()["id"], ordinary.json()["id"]}
    ]

    authorize_as(client, staff)
    notice_list = client.get("/notifications")
    assert notice_list.status_code == 200, notice_list.text
    assert notice_list.headers["cache-control"] == "private, no-store"
    notices = {
        item["target_id"]: item
        for item in notice_list.json()["items"]
        if item["target_type"] == "shared_publication_comment"
    }
    assert set(notices) == {reply.json()["id"], ordinary.json()["id"]}
    mention = notices[reply.json()["id"]]
    assert mention["type"] == "mentioned" and mention["actor_user_id"] is None
    assert mention["snapshot"]["actor_name"] == participation.display_label
    assert mention["snapshot"]["excerpt"] is None
    assert mention["context"]["thread_id"] == thread
    assert mention["context"]["publication_id"] == share["id"]
    assert notices[ordinary.json()["id"]]["type"] == "shared_comment_replied"
    assert client.get(internal, params={"thread_id": thread}).json()["items"] == [
        root.json()
    ]
    notice = f"/notifications/{mention['id']}/read"
    assert client.post(notice).status_code == 200
    assert client.get("/notifications/unread-count").json()["count"] == 1

    db.execute(
        update(ProjectMember)
        .where(
            ProjectMember.project_id == ctx["project"].id,
            ProjectMember.user_id == staff.id,
        )
        .values(deleted_at=datetime.now(UTC))
    )
    db.commit()
    assert client.get("/notifications/unread-count").json()["count"] == 0
    assert client.get("/notifications").json()["items"] == []
    assert client.post(notice).status_code == 404
    authorize_as(client, guest)
    assert client.get(candidates).json() == []
    assert (
        client.post(shared, json={**data, "request_id": str(uuid4())}).status_code
        == 400
    )


def test_shared_mentions_only_use_current_grants_and_notify_once(
    client, access_fixture
):
    ctx = access_fixture
    (first, first_part), (second, second_part) = ctx["guests"]
    share, _, original_share = publish(
        client,
        ctx,
        both=True,
        recipient_options={first_part.id: {"comment_allowed": True}},
    )
    base = shared_path(ctx, "/comments")
    staff_base = f"{publication_base(ctx)}/{share['id']}"
    grants = client.get(staff_base + "/grants").json()["items"]
    grants = {grant["participation_id"]: grant["id"] for grant in grants}
    second_grant = grants[str(second_part.id)]

    authorize_as(client, first)
    response = client.get(base + "/mention-candidates", params={"q": "確認2"})
    assert response.status_code == 200, response.text
    (candidate,) = response.json()
    assert set(candidate) == {"id", "name"}
    assert candidate["name"] == second_part.display_label
    assert candidate["id"] != second_grant
    assert response.headers["cache-control"] == "private, no-store"
    text = "🙂 @" + candidate["name"]
    occurrence = {
        "start": 3,
        "end": 4 + len(candidate["name"]),
        "display_name": candidate["name"],
    }
    mention = {**occurrence, "recipient_id": candidate["id"]}
    data = {"body": text, "request_id": str(uuid4()), "mentions": [mention]}
    for invalid in (
        {**mention, "recipient_id": str(uuid4())},
        {**mention, "recipient_id": second_grant},
        {**mention, "recipient_id": grants[str(first_part.id)]},
        {**mention, "start": 2},
    ):
        assert (
            client.post(base, json={**data, "mentions": [invalid]}).status_code == 400
        )
    response = client.post(base, json=data)
    assert response.status_code == 201, response.text
    comment = response.json()
    assert comment["mentions"] == [occurrence]
    assert candidate["id"] not in response.text and second_grant not in response.text
    assert client.post(base, json=data).json() == comment
    assert client.post(base, json={**data, "mentions": []}).status_code == 409
    assert client.get(base, params={"thread_id": comment["id"]}).json()["items"] == [
        comment
    ]
    assert client.get(base, params={"thread_id": str(uuid4())}).status_code == 404
    assert client.post(f"/shared/activity/{comment['id']}/read").status_code == 404

    authorize_as(client, second)
    assert client.get(base + "/mention-candidates").status_code == 403
    assert client.get("/shared/comments").json()["items"][0]["mentions"] == [occurrence]
    notices = client.get("/shared/activity").json()["items"]
    matches = [item for item in notices if item["id"] == comment["id"]]
    assert len(matches) == 1 and matches[0]["kind"] == "shared_comment_mention"
    assert matches[0]["thread_id"] == comment["id"]
    comment_inbox = {"comments_only": True, "unread_only": True}
    filtered = client.get("/shared/activity", params=comment_inbox).json()
    assert filtered == {"items": matches, "total": 1, "unread": 1}
    assert client.post(f"/shared/activity/{comment['id']}/read").json()["read_at"]
    assert client.get("/shared/activity", params=comment_inbox).json() == {
        "items": [],
        "total": 0,
        "unread": 0,
    }
    second_headers = dict(client.headers)
    second_cookies = dict(client.cookies)

    authorize_as(client, ctx["user"])
    assert client.get(staff_base + "/comments/mention-candidates").status_code == 200
    response = client.post(
        f"{staff_base}/grants/{second_grant}/revoke",
        json={"request_id": str(uuid4()), "version": 1, "reason": "共有期間終了"},
    )
    assert response.status_code == 200, response.text
    client.headers.clear()
    client.headers.update(second_headers)
    client.cookies.clear()
    for name, value in second_cookies.items():
        client.cookies.set(name, value, domain="testserver.local", path="/")
    assert client.get("/shared/activity").json()["items"] == []
    assert client.post(f"/shared/activity/{comment['id']}/read").status_code == 404
    authorize_as(client, first)
    assert client.get(base + "/mention-candidates").json() == []
    assert client.post(base, json=data).json() == comment
    assert (
        client.post(base, json={**data, "request_id": str(uuid4())}).status_code == 400
    )
    authorize_as(client, ctx["user"])
    recipients = original_share["recipients"]
    assert isinstance(recipients, list)
    response = client.post(
        staff_base + "/grants",
        json={
            **original_share,
            "request_id": str(uuid4()),
            "expected_state": "published",
            "publication_version": share["version"],
            "recipients": recipients[1:],
        },
    )
    assert response.status_code == 200, response.text
    authorize_as(client, first)
    (new_candidate,) = client.get(base + "/mention-candidates").json()
    assert new_candidate["name"] == candidate["name"]
    assert new_candidate["id"] != candidate["id"]
    authorize_as(client, second)
    assert client.get(base).status_code == 200
    assert client.post(f"/shared/activity/{comment['id']}/read").status_code == 404
    assert all(
        item["kind"] != "shared_comment_mention"
        for item in client.get("/shared/activity").json()["items"]
    )


def test_shared_link_titles_only_return_currently_visible_destinations(
    client, access_fixture
):
    ctx = access_fixture
    first, _ = ctx["guests"][0]
    other, _ = ctx["guests"][1]
    share, _, _ = publish(client, ctx)
    prepared, _ = create(client, ctx)
    route = "/shared/publications/link-titles"
    ids = [share["id"], prepared["content"]["id"], str(uuid4()), share["id"]]
    authorize_as(client, first)
    response = client.get(route, params={"ids": ids})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    (item,) = response.json()
    assert set(item) == {"id", "title", "expires_at"}
    assert (
        item["id"] == share["id"]
        and item["title"] == ctx["publication"]["content"]["title"]
    )
    assert client.get(route, params={"ids": [str(uuid4())] * 51}).status_code == 422
    first_headers = dict(client.headers)
    first_cookies = dict(client.cookies)
    authorize_as(client, other)
    assert client.get(route, params={"ids": ids}).json() == []
    authorize_as(client, ctx["user"])
    base = f"{publication_base(ctx)}/{share['id']}"
    (grant,) = client.get(base + "/grants").json()["items"]
    response = client.post(
        f"{base}/grants/{grant['id']}/revoke",
        json={"request_id": str(uuid4()), "version": 1, "reason": "確認完了"},
    )
    assert response.status_code == 200, response.text
    client.headers.clear()
    client.headers.update(first_headers)
    client.cookies.clear()
    for name, value in first_cookies.items():
        client.cookies.set(name, value, domain="testserver.local", path="/")
    assert client.get(route, params={"ids": ids}).json() == []
