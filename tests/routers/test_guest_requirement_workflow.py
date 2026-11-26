"""外部の公開範囲、本人の操作と内部の進捗の整合を実DBで確認する。"""

from uuid import uuid4

from app.services.artifact_release_snapshot import digest
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_publications import create, nodes, publication_base
from tests.routers.test_artifact_releases import context as context  # noqa: F401
from tests.routers.test_artifact_releases import preview_data, save
from tests.routers.test_guest_fixed_approvals import guest_login
from tests.routers.test_publication_access import access_fixture as access_fixture  # noqa: F401
from tests.routers.test_publication_access import publish
from tests.routers.test_test_designs import graph


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

    guest_login(client, db, ctx, verified=False)
    assert client.get(path).json()["code"] == "MFA_REQUIRED"
    guest_login(client, db, ctx)
    assert client.get(inbox).json()["total"] == 1
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


def publish_document_for_approval(client, ctx, *, include=False, test_design=None):
    """現在の定義書全体を、承認で使う形式で固定して二人へ共有する。"""
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
    publish(client, ctx, both=True)
    content = ctx["publication"]["content"]
    publication_id = content["id"]
    grants = client.get(f"{base}/{publication_id}/grants").json()["items"]
    grant = next(
        row for row in grants if row["participation_id"] == str(ctx["guests"][0][1].id)
    )
    return release, content, grant


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
