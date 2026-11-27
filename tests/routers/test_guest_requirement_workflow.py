"""外部の公開範囲、本人の操作と内部の進捗の整合を実DBで確認する。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import update

from app.models.requirement_workflow import RequirementWorkflowRequest
from app.services.artifact_release_snapshot import digest
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_publications import create, nodes, publication_base
from tests.routers.test_artifact_releases import context as context  # noqa: F401
from tests.routers.test_artifact_releases import preview_data, save
from tests.routers.test_guest_fixed_approvals import guest_login
from tests.routers.test_publication_access import access_fixture as access_fixture  # noqa: F401
from tests.routers.test_publication_access import publish
from tests.routers.test_test_designs import graph


def test_standalone_review_creates_and_cancels_only_its_own_grant(
    client, db, access_fixture
):
    ctx = access_fixture
    release, _ = save(client, ctx, preview_data(ctx, kind="requirement", include=False))
    ctx["publication"], _ = create(client, ctx, release)
    content = ctx["publication"]["content"]
    identity = content["id"]
    history = f"{publication_base(ctx)}/{identity}/grants"
    route = f"/projects/{ctx['project'].id}/guest-requirement-reviews"
    public = f"/shared/publications/{identity}"
    target = next(
        node
        for node in nodes(client, publication_base(ctx), identity)
        if node["node_kind"] == "requirement"
    )
    actor, participant = ctx["guests"][0]
    reviewer, other = ctx["guests"][1]
    expiry = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    share = dict(
        expected_state="prepared",
        publication_version=1,
        public_digest=content["public_digest"],
        confirmed_full_content=True,
        recipients=[
            dict(
                participation_id=str(participant.id),
                version=participant.version,
                expires_at=expiry,
                download_allowed=False,
            )
        ],
    )
    command = dict(
        publication_id=identity,
        target_node_id=target["id"],
        request_key=str(uuid4()),
        expected_public_digest=content["public_digest"],
        sharing=share,
    )
    actor.password_change_required = True
    db.commit()
    failed = client.post(route, json=command)
    assert failed.status_code == 400, failed.text
    assert client.get(history).json()["items"] == []
    actor.password_change_required = False
    db.commit()

    independent = {
        **share,
        "request_id": str(uuid4()),
        "recipients": [
            dict(
                participation_id=str(other.id),
                version=other.version,
                expires_at=expiry,
                download_allowed=False,
            )
        ],
    }
    published = client.post(
        f"{publication_base(ctx)}/{identity}/publish", json=independent
    )
    assert published.status_code == 200, published.text
    original_grant = client.get(history).json()["items"][0]
    mixed_command = {
        **command,
        "sharing": {**share, "expected_state": "published", "publication_version": 2},
        "grant_ids": [original_grant["id"]],
    }
    response = client.post(route, json=mixed_command)
    assert response.status_code == 201, response.text
    request = response.json()
    verified_replay = client.post(route, json=mixed_command)
    assert (
        verified_replay.status_code == 200
        and verified_replay.json()["id"] == request["id"]
    )
    grants = {
        item["participation_id"]: item for item in client.get(history).json()["items"]
    }
    derived = grants[str(participant.id)]
    assert derived["decision_only"] and not grants[str(other.id)]["decision_only"]
    management = (
        f"/projects/{ctx['project'].id}/guest-participations/{participant.id}/decisions"
    )
    result = client.get(management)
    assert result.status_code == 200, result.text
    (record,) = result.json()["items"]
    assert record["id"] == request["id"] and record["publication_id"] == identity
    assert record["kind"] == "review" and record["target_kind"] == "requirement"
    assert record["state"] == "requested" and record["response_state"] == "pending"
    assignment = f"{public}/requirement-reviews/{request['id']}"
    guest_login(client, db, ctx)
    assert client.get(assignment).status_code == 200
    assert {
        node["node_kind"] for node in client.get(public + "/nodes").json()["items"]
    } == {"requirement", "requirement_detail"}
    notices = client.get("/shared/activity").json()["items"]
    assert any(
        item["id"] == request["id"] and item["kind"] == "requirement_review_requested"
        for item in notices
    )
    deadline = update(RequirementWorkflowRequest).where(
        RequirementWorkflowRequest.id == request["id"]
    )
    db.execute(
        deadline.values(
            due_on=datetime.now(ZoneInfo("Asia/Tokyo")).date() - timedelta(days=1)
        )
    )
    db.commit()
    assert client.get(public).status_code == 200
    assert client.post(f"/shared/activity/{request['id']}/read").status_code == 404
    db.execute(deadline.values(due_on=None))
    db.commit()
    guest_login(client, db, ctx, index=1)
    assert any(
        item["id"] == request["id"]
        for item in client.get("/shared/activity").json()["items"]
    )

    authorize_as(client, ctx["user"])
    cancelled = client.post(
        f"{route}/{request['id']}/cancel", json={"version": 1, "reason": "依頼の見直し"}
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    after = {item["id"]: item for item in client.get(history).json()["items"]}
    assert after[derived["id"]]["effective_status"] == "revoked"
    assert after[original_grant["id"]] == original_grant
    guest_login(client, db, ctx)
    assert client.get(assignment).status_code == 404
    assert client.get(public).status_code == 404
    guest_login(client, db, ctx, index=1)
    assert client.get(public).status_code == 200
    assert client.get(assignment).status_code == 404
    activity = client.get("/shared/activity")
    assert activity.status_code == 200, activity.text
    assert request["id"] not in {item["id"] for item in activity.json()["items"]}


def test_guest_review_of_single_requirement_does_not_share_its_document(
    client, db, access_fixture
):
    ctx = access_fixture
    release, _ = save(client, ctx, preview_data(ctx, kind="requirement", include=False))
    ctx["publication"], _ = create(client, ctx, release)
    publish(client, ctx)
    content = ctx["publication"]["content"]
    identity = content["id"]
    project = f"/projects/{ctx['project'].id}"
    grant = client.get(f"{publication_base(ctx)}/{identity}/grants").json()["items"][0]
    target = next(
        item
        for item in nodes(client, publication_base(ctx), identity)
        if item["node_kind"] == "requirement"
    )
    command = {
        "publication_id": identity,
        "target_node_id": target["id"],
        "grant_ids": [grant["id"]],
        "request_key": str(uuid4()),
        "expected_public_digest": content["public_digest"],
    }
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    expired = client.post(
        project + "/guest-requirement-reviews",
        json={**command, "due_on": (today - timedelta(days=1)).isoformat()},
    )
    assert expired.status_code == 400, expired.text
    created = client.post(
        project + "/guest-requirement-reviews",
        json={**command, "due_on": today.isoformat()},
    )
    assert created.status_code == 201, created.text
    assert created.json()["scope_kind"] == "requirement"
    guest_login(client, db, ctx)
    shared = f"/shared/publications/{identity}"
    expected = {"requirement", "requirement_detail"}
    assert {
        item["node_kind"] for item in client.get(shared + "/nodes").json()["items"]
    } == expected
    assigned = shared + f"/requirement-reviews/{created.json()['id']}"
    assert (
        datetime.fromisoformat(client.get(assigned).json()["expires_at"]).date()
        == today
    )
    assert {
        item["node_kind"] for item in client.get(assigned + "/nodes").json()["items"]
    } == expected
    submitted = client.post(
        assigned + "/submission", json={"version": 1, "confirm_remaining": True}
    )
    assert submitted.status_code == 200, submitted.text


def test_guest_review_uses_only_assigned_shared_scope(client, db, access_fixture):
    """同じ公開版の別の閲覧者、範囲外要件と失効済み許可を拒否する。"""
    ctx = access_fixture
    supplied = preview_data(ctx)
    release, _ = save(client, ctx, supplied)
    ctx["publication"], _ = create(client, ctx, release)
    publish(client, ctx, both=True)
    identity = ctx["publication"]["content"]["id"]
    project = f"/projects/{ctx['project'].id}"
    internal = project + "/guest-requirement-reviews"
    public = f"/shared/publications/{identity}"
    profile = client.put(
        project + "/guest-profile", json={"name": "公開した確認案件", "version": 0}
    )
    assert profile.status_code == 200, profile.text
    option_response = client.get(
        internal + "/options", params={"publication_id": identity}
    )
    assert option_response.status_code == 200, option_response.text
    options = option_response.json()
    all_nodes = nodes(client, publication_base(ctx), identity)
    target = next(n for n in all_nodes if n["node_kind"] == "section")
    requirement = next(n for n in all_nodes if n["node_kind"] == "requirement")
    document = next(n for n in all_nodes if n["node_kind"] == "requirement_document")
    grant_rows = client.get(f"{publication_base(ctx)}/{identity}/grants").json()[
        "items"
    ]
    grant = next(
        row
        for row in grant_rows
        if row["participation_id"] == str(ctx["guests"][0][1].id)
    )
    body = dict(
        publication_id=identity,
        target_node_id=target["id"],
        grant_ids=[grant["id"]],
        request_key=str(uuid4()),
        expected_public_digest=options["public_digest"],
        note="この章をご確認ください",
    )
    created = client.post(internal, json=body)
    assert created.status_code == 201, created.text
    request = created.json()
    assert request["external_review"] is True
    replay = client.post(internal, json=body)
    assert replay.status_code == 200 and replay.json()["id"] == request["id"]
    ctx["spec"].summary = "公開後の編集内容"
    db.commit()
    stale = client.post(internal, json={**body, "request_key": str(uuid4())})
    assert stale.status_code == 409, stale.text
    replay = client.post(internal, json=body)
    assert replay.status_code == 200 and replay.json()["id"] == request["id"]
    path = f"{public}/requirement-reviews/{request['id']}"
    inbox = "/shared/publications/requirement-reviews"

    guest_login(client, db, ctx, index=1)
    assert client.get(public).status_code == 200
    assert client.get(path).status_code == 404
    assert client.get(inbox).json()["total"] == 0
    other = client.get("/shared/publications").json()["items"][0]
    assert other["own_decision_status"] == "none"
    assert "decision_refresh_at" not in other

    guest_login(client, db, ctx, verified=False)
    assert client.get(path).json()["code"] == "MFA_REQUIRED"
    before_mfa = client.get("/shared/publications").json()["items"][0]
    assert "own_decision_status" not in before_mfa
    assert "decision_refresh_at" not in before_mfa
    guest_login(client, db, ctx)
    assert (
        client.get("/shared/publications").json()["items"][0]["own_decision_status"]
        == "pending"
    )
    assigned = client.get(inbox).json()
    assert assigned["total"] == 1
    (entry,) = assigned["items"]
    assert entry["public_project_name"] == "公開した確認案件"
    assert entry["captured_at"] == ctx["publication"]["content"]["captured_at"]
    read = client.get(path)
    assert read.status_code == 200, read.text
    assert read.headers["cache-control"] == "private, no-store"
    assert read.json()["target_node_id"] == target["id"]
    assert not ({"project_id", "release_id", "participants"} & read.json().keys())
    assert (
        client.get(project + f"/requirement-workflow/{request['id']}").status_code
        == 403
    )
    first = client.get(path + "/nodes", params={"page_size": 1}).json()
    assert first["items"][0]["id"] == target["id"]
    assert first["items"][0]["parent_id"] is None
    scoped_node = first["items"][0]
    assert scoped_node["content_digest"] == digest(
        {key: value for key, value in scoped_node.items() if key != "content_digest"}
    )
    second = client.get(path + "/nodes", params={"cursor": first["next_cursor"]}).json()
    assert second["items"][0]["id"] == requirement["id"]
    attachment = next(n for n in second["items"] if n["node_kind"] == "specification")
    assert attachment["payload"]["summary"] == "入力の保護"
    assert document["id"] not in {n["id"] for n in second["items"]}
    assert (
        client.put(
            path + f"/nodes/{document['id']}", json={"confirmed": True, "version": 1}
        ).status_code
        == 404
    )
    comment = client.post(
        path + "/threads",
        json={
            "node_id": requirement["id"],
            "version": 1,
            "kind": "comment",
            "body": "承知しました",
        },
    )
    assert comment.status_code == 201, comment.text
    correction = client.post(
        path + "/threads",
        json={
            "node_id": requirement["id"],
            "version": 2,
            "kind": "correction",
            "body": "保持する入力範囲を明記してください",
            "anchor": {"field": "description", "quote": "本文", "quote_start": 2},
        },
    )
    assert correction.status_code == 201, correction.text
    thread_id = correction.json()["id"]
    assert correction.json()["status"] == "open"
    assert (
        client.post(
            f"{path}/threads/{thread_id}/actions",
            json={"version": 1, "action": "resolve"},
        ).status_code
        == 403
    )

    authorize_as(client, ctx["user"])
    work_path = f"{project}/requirement-workflow/{request['id']}"
    internal_thread = f"{work_path}/review-threads/{thread_id}"
    thread = client.get(internal_thread).json()["thread"]
    assert (thread["quote"], thread["quote_start"]) == ("本文", 2)
    started = client.post(
        internal_thread + "/actions", json={"version": 1, "action": "start"}
    )
    assert started.status_code == 200, started.text
    response = client.post(
        internal_thread + "/actions",
        json={"version": 2, "action": "respond", "body": "入力欄の値を全て保持します"},
    )
    assert response.status_code == 200, response.text

    guest_login(client, db, ctx)
    replies = client.get(f"{path}/threads/{thread_id}/replies").json()["items"]
    assert replies == ["入力欄の値を全て保持します"]
    resolved = client.post(
        f"{path}/threads/{thread_id}/actions",
        json={"version": 3, "action": "resolve"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"
    latest = client.get(path).json()
    check = client.put(
        path + f"/nodes/{requirement['id']}",
        json={"confirmed": True, "version": latest["version"]},
    )
    assert check.status_code == 200, check.text
    assert check.json()["confirmed_count"] == 1
    result = client.post(
        path + "/submission", json={"version": check.json()["version"]}
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "completed"

    authorize_as(client, ctx["user"])
    assert (
        client.get(project + f"/requirement-workflow/{request['id']}").json()["status"]
        == "completed"
    )
    revoked = client.post(
        f"{publication_base(ctx)}/{identity}/grants/{grant['id']}/revoke",
        json={
            "request_id": str(uuid4()),
            "version": grant["version"],
            "reason": "確認完了",
        },
    )
    assert revoked.status_code == 200, revoked.text
    guest_login(client, db, ctx)
    assert client.get(path).status_code == 404
    assert client.get(path + "/nodes").status_code == 404
    assert client.get(inbox).json()["total"] == 0


def prepare_document_for_approval(client, ctx, *, include=False, test_design=None):
    """現在の定義書全体を承認で使う形式で固定する。"""
    preview_input = {
        **preview_data(ctx, include=include),
        "projection_version": "internal-preview-v2",
    }
    if test_design:
        preview_input["included_sources"].append(
            {
                "kind": "test_design",
                "id": test_design["id"],
                "version": test_design["version"],
            }
        )
    release, _ = save(client, ctx, preview_input)
    base = publication_base(ctx)
    publication_input = {
        "release_id": release["id"],
        "projection_version": "public-preview-v2",
        "request_id": str(uuid4()),
    }
    preview = client.post(base + "/preview", json=publication_input)
    assert preview.status_code == 200, preview.text
    publication = client.post(
        base,
        json={
            **publication_input,
            "expected_public_digest": preview.json()["public_digest"],
            "expected_source_manifest_digest": preview.json()["source_manifest_digest"],
        },
    )
    assert publication.status_code == 201, publication.text
    ctx["publication"] = publication.json()
    return release, ctx["publication"]["content"]


def publish_document_for_approval(client, ctx, *, include=False, test_design=None):
    """現在の固定版を二人へ共有する。"""
    release, content = prepare_document_for_approval(
        client, ctx, include=include, test_design=test_design
    )
    publish(client, ctx, both=True)
    publication_id = content["id"]
    grants = client.get(f"{publication_base(ctx)}/{publication_id}/grants").json()[
        "items"
    ]
    grant = next(
        row for row in grants if row["participation_id"] == str(ctx["guests"][0][1].id)
    )
    return release, content, grant


def test_document_approval_creates_and_revokes_dependent_share(
    client, db, access_fixture
):
    ctx = access_fixture
    _, content = prepare_document_for_approval(client, ctx)
    identity = content["id"]
    base = f"{publication_base(ctx)}/{identity}"
    route = f"/projects/{ctx['project'].id}/guest-requirement-approvals"
    participant = ctx["guests"][0][1]
    expiry = datetime.now(UTC) + timedelta(days=1)
    options = {
        "publication_id": identity,
        "expected_public_digest": content["public_digest"],
        "due_on": datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat(),
        "sharing": {
            "expected_state": "prepared",
            "publication_version": 1,
            "public_digest": content["public_digest"],
            "confirmed_full_content": True,
            "recipients": [
                {
                    "participation_id": str(participant.id),
                    "version": participant.version,
                    "expires_at": expiry.isoformat(),
                    "download_allowed": False,
                }
            ],
        },
    }
    blocked = client.post(route + "/preview", json=options)
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["review_required"] and not blocked.json()["can_request"]
    ctx["project"].requirement_review_required = False
    db.commit()
    ready = client.post(route + "/preview", json=options)
    assert ready.status_code == 200, ready.text
    assert ready.json()["can_request"]
    assert client.get(base).json()["state"] == "prepared"
    assert client.get(base + "/grants").json()["items"] == []
    command = {
        **options,
        "expected_digest": ready.json()["confirmation_digest"],
        "request_key": str(uuid4()),
        "confirmed_content": True,
    }
    failed = client.post(route, json={**command, "note": "確認前の変更"})
    assert failed.status_code == 409, failed.text
    assert client.get(base + "/grants").json()["items"] == []
    created = client.post(route, json=command)
    assert created.status_code == 201, created.text
    assert created.json()["external_approval"] is True
    assert client.post(route, json=command).status_code == 200
    (grant,) = client.get(base + "/grants").json()["items"]
    assert grant["decision_only"] is True
    request = created.json()["id"]
    guest_login(client, db, ctx)
    publication = f"/shared/publications/{identity}"
    approval = f"{publication}/requirement-approvals/{request}"
    assert client.get(approval).json()["can_decide"] is True
    activity = client.get("/shared/activity", params={"unread_only": True})
    assert activity.status_code == 200, activity.text
    notice = next(item for item in activity.json()["items"] if item["id"] == request)
    assert notice["kind"] == "requirement_approval_requested"
    assert notice["publication_id"] == identity
    assert client.post(f"/shared/activity/{request}/read").json()["read_at"]
    authorize_as(client, ctx["user"])
    cancelled = client.post(
        f"{route}/{request}/cancel", json={"version": 1, "reason": "承認範囲を再検討"}
    )
    assert cancelled.status_code == 200, cancelled.text
    assert (
        client.get(base + "/grants").json()["items"][0]["effective_status"] == "revoked"
    )
    guest_login(client, db, ctx)
    assert client.get(approval).status_code == 404
    assert client.get(publication).status_code == 404


def test_guest_and_internal_approval_share_one_development_baseline(
    client, db, access_fixture, create_test_user, assign_project_role
):
    """同じ文書全体の判断を集約し、本人以外と対象改変を拒否する。"""
    ctx = access_fixture
    project_id = ctx["project"].id
    approver = create_test_user(email="formal-approver@example.com")
    assign_project_role(user=approver, project=ctx["project"], role_key="project_admin")
    authorize_as(client, ctx["user"])
    release, content, grant = publish_document_for_approval(client, ctx, include=True)
    publication_id = content["id"]
    base = publication_base(ctx)
    formal = f"/projects/{project_id}/guest-requirement-approvals"
    document = f"/projects/{project_id}/requirement-documents/{ctx['doc'].id}"
    baseline = document + "/formal-approval-baseline"
    options = {
        "publication_id": publication_id,
        "expected_public_digest": content["public_digest"],
        "grant_ids": [grant["id"]],
        "participant_ids": [approver.id],
    }
    preview = client.post(formal + "/preview", json=options)
    assert preview.status_code == 200, preview.text
    assert preview.json()["review_required"] and not preview.json()["can_request"]
    document_node = next(
        node
        for node in nodes(client, base, publication_id)
        if node["node_kind"] == "requirement_document"
    )
    review = client.post(
        f"/projects/{project_id}/guest-requirement-reviews",
        json={
            "publication_id": publication_id,
            "expected_public_digest": content["public_digest"],
            "grant_ids": [grant["id"]],
            "target_node_id": document_node["id"],
            "request_key": str(uuid4()),
        },
    )
    assert review.status_code == 201, review.text
    review_id = review.json()["id"]
    guest_login(client, db, ctx)
    submitted = client.post(
        f"/shared/publications/{publication_id}/requirement-reviews/{review_id}/submission",
        json={"version": 1, "confirm_remaining": True},
    )
    assert submitted.status_code == 200, submitted.text
    authorize_as(client, ctx["user"])
    preview = client.post(formal + "/preview", json=options)
    assert preview.status_code == 200, preview.text
    assert preview.json()["can_request"], preview.text
    assert preview.json()["reviewed_request_ids"] == [review_id]
    payload = {
        **options,
        "expected_digest": preview.json()["confirmation_digest"],
        "request_key": str(uuid4()),
        "confirmed_content": True,
    }
    created = client.post(formal, json=payload)
    assert created.status_code == 201, created.text
    request = created.json()
    assert (
        request["scope_kind"] == "requirement_document"
        and request["release_id"] == release["id"]
    )
    assert client.post(formal, json=payload).json()["id"] == request["id"]
    assert client.get(baseline).json() is None

    public = (
        f"/shared/publications/{publication_id}/requirement-approvals/{request['id']}"
    )
    guest_login(client, db, ctx, index=1)
    assert client.get(f"/shared/publications/{publication_id}").status_code == 200
    assert client.get(public).status_code == 404
    guest_login(client, db, ctx, verified=False)
    assert client.get(public).json()["code"] == "MFA_REQUIRED"
    guest_login(client, db, ctx)
    assert client.get("/shared/publications/requirement-approvals").json()["total"] == 1
    own = client.get(public)
    assert own.status_code == 200, own.text
    assert own.json()["can_decide"] and own.json()["previous_publication_id"] is None
    assert not ({"release_id", "approval_basis", "participants"} & own.json().keys())
    decision = {
        "version": own.json()["version"],
        "expected_public_digest": content["public_digest"],
        "operation_key": str(uuid4()),
        "decision": "approved",
        "confirmed_content": True,
    }
    assert (
        client.post(
            public + "/decision", json={**decision, "decision": "returned"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            public + "/decision", json={**decision, "expected_public_digest": "0" * 64}
        ).status_code
        == 409
    )
    first = client.post(public + "/decision", json=decision)
    assert first.status_code == 200, first.text
    assert first.json()["responded"] and first.json()["status"] == "requested"
    assert client.post(public + "/decision", json=decision).status_code == 200

    authorize_as(client, approver)
    assert client.get(baseline).json() is None
    internal = (
        f"/projects/{project_id}/requirement-workflow/{request['id']}"
        "/formal-approval-decision"
    )
    current = client.get(internal).json()
    final = client.post(
        internal,
        json={
            "version": current["request"]["version"],
            "expected_release_digest": current["release_digest"],
            "operation_key": str(uuid4()),
            "decision": "approved",
            "confirmed_content": True,
        },
    )
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert client.get(baseline).json()["id"] == request["id"]


def test_shared_comparison_requires_both_publication_grants(client, db, access_fixture):
    """前回の開発基準を公開用IDで比較し、旧版の許可だけ失効しても本文を漏らさない。"""
    ctx = access_fixture
    project_id = ctx["project"].id
    project_path = f"/projects/{project_id}"
    document = f"{project_path}/requirement-documents/{ctx['doc'].id}"
    base = publication_base(ctx)
    formal = project_path + "/guest-requirement-approvals"
    requirement = ctx["req"]
    document_revision = ctx["doc"].version

    def request_approval(test_design=None):
        _, content, grant = publish_document_for_approval(
            client, ctx, test_design=test_design
        )
        publication_id = content["id"]
        public_nodes = nodes(client, base, publication_id)
        target = next(
            node for node in public_nodes if node["node_kind"] == "requirement_document"
        )
        options = {
            "publication_id": publication_id,
            "expected_public_digest": content["public_digest"],
            "grant_ids": [grant["id"]],
        }
        review = client.post(
            project_path + "/guest-requirement-reviews",
            json={
                **options,
                "target_node_id": target["id"],
                "request_key": str(uuid4()),
            },
        )
        assert review.status_code == 201, review.text
        scope = f"/shared/publications/{publication_id}"
        guest_login(client, db, ctx)
        submitted = client.post(
            f"{scope}/requirement-reviews/{review.json()['id']}/submission",
            json={
                "version": 1,
                "confirm_remaining": True,
            },
        )
        assert submitted.status_code == 200, submitted.text
        authorize_as(client, ctx["user"])
        preview = client.post(formal + "/preview", json=options)
        assert preview.status_code == 200 and preview.json()["can_request"], (
            preview.text
        )
        result = client.post(
            formal,
            json={
                **options,
                "request_key": str(uuid4()),
                "confirmed_content": True,
                "expected_digest": preview.json()["confirmation_digest"],
            },
        )
        assert result.status_code == 201, result.text
        return (
            content,
            grant,
            public_nodes,
            f"{scope}/requirement-approvals/{result.json()['id']}",
        )

    designs = f"{project_path}/test-designs"
    design = client.post(designs, json={"name": "公開テスト設計"})
    assert design.status_code == 201, design.text
    design_url = f"{designs}/{design.json()['id']}"
    saved_design = client.put(design_url, json=graph(design.json()))
    assert saved_design.status_code == 200, saved_design.text
    previous, old_grant, old_nodes, first = request_approval(saved_design.json())
    guest_login(client, db, ctx)
    assert client.get(first + "/comparison").status_code == 404
    response = client.post(
        first + "/decision",
        json={
            "version": 1,
            "expected_public_digest": previous["public_digest"],
            "operation_key": str(uuid4()),
            "decision": "approved",
            "confirmed_content": True,
        },
    )
    assert response.status_code == 200 and response.json()["status"] == "approved", (
        response.text
    )
    authorize_as(client, ctx["user"])
    changed = client.patch(
        document, json={"version": document_revision, "purpose": "更新後の目的"}
    )
    assert changed.status_code == 200, changed.text
    db.refresh(ctx["doc"])
    changed = client.patch(
        f"{project_path}/requirements/{requirement.id}",
        json={
            "version": requirement.version,
            "acceptance_criteria": "更新後の受け入れ条件",
        },
    )
    assert changed.status_code == 200, changed.text
    edited_design = saved_design.json()
    edited_design["items"][0]["content"] = "更新された試験内容"
    updated_test = client.put(design_url, json=edited_design)
    assert updated_test.status_code == 200, updated_test.text
    current, _, new_nodes, second = request_approval(updated_test.json())

    guest_login(client, db, ctx, index=1)
    assert client.get(second + "/comparison").status_code == 404
    guest_login(client, db, ctx)
    meta = client.get(second).json()
    assert meta["previous_publication_id"] == previous["id"]
    old_meta = client.get(f"/shared/publications/{previous['id']}").json()
    result = client.get(second + "/comparison", params={"page_size": 1})
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "private, no-store"
    data = result.json()
    assert data["total"] == 3
    assert data["authorization_revision"] == meta["authorization_revision"]
    assert data["previous_authorization_revision"] == old_meta["authorization_revision"]
    assert data["previous_publication_id"] == previous["id"]
    assert data["items"] == [
        {
            "kind": "changed",
            "before_id": old_nodes[0]["id"],
            "after_id": new_nodes[0]["id"],
            "fields": ["purpose"],
        }
    ]
    second_page = client.get(second + "/comparison", params={"page_size": 1, "page": 2})
    assert second_page.status_code == 200, second_page.text
    assert second_page.json()["items"][0]["fields"] == ["acceptance_criteria"]
    third_page = client.get(second + "/comparison", params={"page_size": 1, "page": 3})
    assert third_page.status_code == 200, third_page.text
    assert third_page.json()["items"][0]["fields"] == ["graph_digest"]

    authorize_as(client, ctx["user"])
    revoke = client.post(
        f"{base}/{previous['id']}/grants/{old_grant['id']}/revoke",
        json={
            "request_id": str(uuid4()),
            "version": old_grant["version"],
            "reason": "閲覧期限を終了",
        },
    )
    assert revoke.status_code == 200, revoke.text
    guest_login(client, db, ctx)
    assert client.get(second).json()["previous_publication_id"] is None
    assert client.get(second + "/comparison").status_code == 404
    assert client.get(f"/shared/publications/{current['id']}").status_code == 200
