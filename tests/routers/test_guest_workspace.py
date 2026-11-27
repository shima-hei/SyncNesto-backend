"""公開用案件概要と参加世代の分離を実PostgreSQLで検証する。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.config import settings as app_settings
from app.models.artifact_publication_access import ProjectGuestParticipation
from app.models.project import Project
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_publications import create, nodes, publication_base
from tests.routers.test_artifact_releases import context as context  # noqa: F401
from tests.routers.test_publication_access import (
    access_fixture as access_fixture,  # noqa: F401
)
from tests.routers.test_publication_access import (
    publish,
    shared_path,
)


def test_guest_projects_and_public_profiles(client, db, access_fixture, monkeypatch):
    monkeypatch.setattr(app_settings, "mcp_enabled", True)
    ctx = access_fixture
    user, first = ctx["guests"][0]
    other_user, other = ctx["guests"][1]
    project_id = ctx["project"].id
    second_project = Project(
        tenant_id=ctx["project"].tenant_id,
        project_code="GUEST-2",
        name="社内案件2",
        description="社内限定の説明",
    )
    db.add(second_project)
    db.flush()
    second = ProjectGuestParticipation(
        id=uuid4(),
        project_id=second_project.id,
        user_id=user.id,
        generation=1,
        display_label="確認者",
        expires_at=datetime.now(UTC) + timedelta(days=3),
        created_by=ctx["user"].id,
    )
    db.add(second)
    db.commit()
    first_id, second_id, other_id = map(str, (first.id, second.id, other.id))
    assert client.get("/shared/projects").json()["items"] == []

    settings = f"/projects/{project_id}/guest-profile"
    assert client.get(settings).json()["version"] == 0
    data = {"name": "公開用案件", "description": "共有可能な説明", "version": 0}
    saved = client.put(settings, json=data)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    assert client.put(settings, json={**data, "name": "競合"}).status_code == 409
    assert client.put(settings, json={**data, "tenant_id": 100}).status_code == 422

    authorize_as(client, user)
    response = client.get("/shared/projects")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    rows = {item["participation_id"]: item for item in response.json()["items"]}
    assert response.json()["total"] == 2
    assert rows[first_id]["name"] == "公開用案件"
    assert rows[second_id]["name"] is None
    assert rows[second_id]["description"] is None
    assert set(rows[first_id]) == {
        "name",
        "description",
        "starts_on",
        "ends_on",
        "contact",
        "announcement",
        "participation_id",
        "expires_at",
    }
    assert client.get(f"/shared/projects/{first_id}").json() == rows[first_id]
    assert client.get(f"/shared/projects/{other_id}").status_code == 404
    assert client.get(settings).status_code == 403
    assert client.get("/shared/publications").json()["total"] == 0
    assert client.get("/integrations/mcp/availability").json() == {"can_connect": False}

    authorize_as(client, ctx["user"])
    published, _, _ = publish(client, ctx)
    authorize_as(client, user)
    response = client.get("/shared/publications", params={"participation_id": first_id})
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["id"] == published["id"]
    assert response.json()["items"][0]["public_project_name"] == "公開用案件"
    for unrelated in (second_id, other_id):
        assert (
            client.get(
                "/shared/publications", params={"participation_id": unrelated}
            ).json()["total"]
            == 0
        )

    authorize_as(client, ctx["user"])
    revoked = client.post(
        f"/projects/{project_id}/guest-participations/{first_id}/revoke",
        json={"version": first.version, "request_id": str(uuid4()), "reason": "検証"},
    )
    assert revoked.status_code == 200, revoked.text
    authorize_as(client, user)
    assert client.get(f"/shared/projects/{first_id}").status_code == 404
    assert client.get(f"/shared/projects/{second_id}").status_code == 200
    assert client.get("/shared/publications").json()["total"] == 0
    authorize_as(client, other_user)
    assert client.get(f"/shared/projects/{first_id}").status_code == 404
    assert client.get(f"/shared/projects/{other_id}").status_code == 200


def test_shared_threads_and_replies_require_the_same_current_fixed_version(
    client,
    access_fixture,
):
    ctx = access_fixture
    guest, participant = ctx["guests"][0]
    reader, _ = ctx["guests"][1]
    published, _, _ = publish(
        client,
        ctx,
        both=True,
        recipient_options={participant.id: {"comment_allowed": True}},
    )
    internal = f"{publication_base(ctx)}/{published['id']}/comments"
    shared = shared_path(ctx, "/comments")
    command = {"request_id": str(uuid4()), "body": "ゲストと共有する質問"}
    post = client.post(internal, json=command)
    assert post.status_code == 201, post.text
    root = post.json()
    assert "author_id" not in root

    authorize_as(client, reader)
    response = client.get(shared)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == [root]
    assert response.json()["can_post"] is False
    assert client.post(shared, json=command).status_code == 403

    authorize_as(client, guest)
    notices = client.get("/shared/activity")
    assert notices.status_code == 200, notices.text
    assert notices.json()["unread"] == 1
    notice = notices.json()["items"][0]
    assert notice["kind"] == "publication_shared"
    assert client.post(f"/shared/activity/{notice['id']}/read").status_code == 200
    assert client.get("/shared/activity").json()["unread"] == 0
    guest_root = client.post(
        shared, json={"request_id": str(uuid4()), "body": "導入前の確認"}
    )
    assert guest_root.status_code == 201, guest_root.text
    reply = {
        "request_id": str(uuid4()),
        "body": "ゲストからの返信",
        "parent_id": root["id"],
    }
    posted = client.post(shared, json=reply)
    assert posted.status_code == 201, posted.text
    assert client.post(shared, json=reply).json() == posted.json()
    assert client.post(shared, json={**reply, "body": "別の返信"}).status_code == 409
    assert client.get(shared, params={"parent_id": root["id"]}).json()["items"] == [
        posted.json()
    ]
    inbox = client.get("/shared/comments")
    assert inbox.status_code == 200, inbox.text
    assert {item["id"] for item in inbox.json()["items"]} == {
        root["id"],
        guest_root.json()["id"],
    }
    assert inbox.headers["cache-control"] == "private, no-store"
    assert client.get(shared.split("/comments")[0] + "/files").json()["items"] == []

    authorize_as(client, ctx["user"])
    staff_reply = client.post(
        internal,
        json={
            "request_id": str(uuid4()),
            "parent_id": guest_root.json()["id"],
            "body": "確認への返信",
        },
    )
    assert staff_reply.status_code == 201, staff_reply.text
    authorize_as(client, guest)
    unread = client.get("/shared/activity", params={"unread_only": True})
    assert unread.json()["unread"] == 1
    assert unread.json()["items"][0]["kind"] == "shared_comment_reply"
    assert "body" not in unread.json()["items"][0]
    assert unread.json()["items"][0]["thread_id"] == guest_root.json()["id"]
    authorize_as(client, reader)
    assert client.get("/shared/activity").json()["total"] == 0
    authorize_as(client, ctx["user"])
    path = f"/projects/{ctx['project'].id}/guest-participations/{participant.id}/revoke"
    revoked = client.post(
        path,
        json={
            "request_id": str(uuid4()),
            "version": participant.version,
            "reason": "検証",
        },
    )
    assert revoked.status_code == 200
    authorize_as(client, guest)
    assert client.get(shared).status_code == 404
    assert client.get("/shared/comments").json()["total"] == 0
    assert client.get("/shared/activity").json()["total"] == 0
    authorize_as(client, reader)
    assert client.get(shared).json()["total"] == 2


def test_shared_quote_uses_exact_unicode_position_on_its_own_fixed_version(
    client, db, access_fixture
):
    ctx = access_fixture
    base = publication_base(ctx)
    unrelated_id = ctx["publication"]["content"]["id"]
    unrelated_node = next(
        node
        for node in nodes(client, base, unrelated_id)
        if node["node_kind"] == "requirement"
    )
    source = "😀 手順の確認。手順の確認。"
    ctx["req"].description = source
    db.commit()
    ctx["publication"], _ = create(client, ctx)
    guest, participant = ctx["guests"][0]
    published, _, _ = publish(
        client, ctx, recipient_options={participant.id: {"comment_allowed": True}}
    )
    requirement = next(
        node
        for node in nodes(client, base, published["id"])
        if node["node_kind"] == "requirement"
    )
    ctx["req"].description = "編集後の本文"
    db.commit()
    authorize_as(client, guest)
    route = shared_path(ctx, "/comments")
    quote = "手順の確認"
    anchor = {
        "field": "description",
        "quote": quote,
        "quote_start": source.rindex(quote),
    }
    command = {
        "request_id": str(uuid4()),
        "body": "2件目の手順へのコメント",
        "node_id": requirement["id"],
        "anchor": anchor,
    }
    assert (
        client.post(
            route, json={**command, "node_id": unrelated_node["id"]}
        ).status_code
        == 404
    )
    wrong = client.post(
        route,
        json={
            **command,
            "anchor": {**anchor, "quote_start": anchor["quote_start"] + 1},
        },
    )
    assert wrong.status_code == 400, wrong.text
    created = client.post(route, json=command)
    assert created.status_code == 201, created.text
    stored = created.json()
    assert stored["anchor"] == anchor
    assert client.post(route, json=command).json() == stored
    assert (
        client.post(
            route,
            json={**command, "anchor": {**anchor, "quote_start": source.index(quote)}},
        ).status_code
        == 409
    )
    inbox = client.get("/shared/comments").json()
    assert inbox["items"][0]["anchor"] == anchor
    reply = client.post(
        route,
        json={
            "request_id": str(uuid4()),
            "parent_id": stored["id"],
            "body": "返信",
            "anchor": anchor,
        },
    )
    assert reply.status_code == 422, reply.text
    authorize_as(client, ctx["user"])
    assert (
        client.get(f"{base}/{published['id']}/comments").json()["items"][0]["anchor"]
        == anchor
    )


def test_change_active_participation_expiry_and_read_its_grants(client, access_fixture):
    ctx = access_fixture
    guest, participant = ctx["guests"][0]
    publication, _, _ = publish(client, ctx)
    identity = str(participant.id)
    route = f"/projects/{ctx['project'].id}/guest-participations/{identity}"
    settings = {
        "request_id": str(uuid4()),
        "version": participant.version,
        "reason": "参加者本人との日程調整",
        "expires_at": (datetime.now(UTC) + timedelta(hours=6)).isoformat(),
    }
    result = client.put(route + "/expiry", json=settings)
    assert result.status_code == 200, result.text
    assert result.json()["version"] == participant.version + 1
    assert client.put(route + "/expiry", json=settings).json() == result.json()
    grants = client.get(route + "/grants")
    assert grants.status_code == 200, grants.text
    assert grants.json()["items"][0]["publication_id"] == publication["id"]
    authorize_as(client, guest)
    overview = client.get(f"/shared/projects/{identity}")
    assert datetime.fromisoformat(
        overview.json()["expires_at"]
    ) == datetime.fromisoformat(settings["expires_at"])
    shared = client.get(shared_path(ctx))
    assert datetime.fromisoformat(
        shared.json()["expires_at"]
    ) == datetime.fromisoformat(settings["expires_at"])
    assert client.get(route + "/grants").status_code == 403
    assert client.put(route + "/expiry", json=settings).status_code == 403

    authorize_as(client, ctx["user"])
    assert (
        client.put(
            route + "/expiry", json={**settings, "request_id": str(uuid4())}
        ).status_code
        == 409
    )
    cancelled = client.post(
        route + "/revoke",
        json={
            "request_id": str(uuid4()),
            "version": result.json()["version"],
            "reason": "検証",
        },
    )
    assert cancelled.status_code == 200
    assert (
        client.put(
            route + "/expiry",
            json={
                **settings,
                "request_id": str(uuid4()),
                "version": cancelled.json()["version"],
            },
        ).status_code
        == 409
    )
