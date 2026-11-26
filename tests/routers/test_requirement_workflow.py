"""レビュー依頼の対象固定と一括保存をHTTPから確認する。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, local
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.exceptions import BadRequestError
from app.main import create_app
from app.models.artifact_release import ArtifactRelease
from app.models.requirement import Requirement, RequirementDetail, RequirementOpenIssue
from app.models.requirement_workflow import RequirementWorkflowRequest
from app.models.user import User
from app.routers.requirement_workflow import service
from tests.helpers.auth import authorize_as


@pytest.fixture
def review_case(
    client,
    db,
    create_test_user,
    create_test_project,
    assign_project_role,
    create_test_requirement_document,
    create_test_requirement_section,
):
    """管理者、レビュー担当者、章に属する要件を用意する。"""
    issuer = create_test_user()
    reviewer = create_test_user(email="reviewer@example.com")
    project = create_test_project()
    assign_project_role(user=issuer, project=project, role_key="project_admin")
    assign_project_role(user=reviewer, project=project, role_key="member")
    document = create_test_requirement_document(project=project)
    section = create_test_requirement_section(
        document=document, content="依頼時点の本文"
    )
    create_test_requirement_section(document=document, content="レビュー範囲外の本文")
    requirement = Requirement(
        document_id=document.id,
        section_id=section.id,
        requirement_code="REV-001",
        requirement_type="functional",
        title="固定内容を読める",
    )
    db.add(requirement)
    db.commit()
    authorize_as(client, issuer)
    project_url = f"/projects/{project.id}"
    return project_url, document, section, requirement, reviewer


def _prepare(client, review_case, *, count=1, participant_ids=None):
    project_url, document, section, _, reviewer = review_case
    url = f"{project_url}/requirement-documents/{document.id}/review-requests"
    payload = {
        "document_version": document.version,
        "scope_kind": "section",
        "scope_id": section.id,
        "participant_ids": participant_ids or [reviewer.id],
    }
    preview = client.post(url + "/preview", json=payload)
    assert preview.status_code == 200, preview.text
    assert preview.json()["requirement_count"] == count
    return url, {
        **payload,
        "request_key": str(uuid4()),
        "expected_digest": preview.json()["confirmation_digest"],
    }


def test_create_review_and_find_assigned_request(client, db, review_case):
    """章の依頼が一つの版に結びつき、担当者から見える。"""
    project_url, document, section, _, reviewer = review_case
    url, data = _prepare(client, review_case)
    assert db.scalar(select(func.count()).select_from(ArtifactRelease)) == 0

    created = client.post(url, json=data)
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["scope_kind"] == "section" and row["scope_id"] == section.id
    assert row["participants"][0]["user_id"] == reviewer.id
    assert row["document_version"] == document.version
    assert row["kind"] == "review"
    assert row["version_label"] == "R-001"
    inbox = f"{project_url}/requirement-workflow"
    for filters in (
        {"q": "r-001", "kind": "review", "status": "requested", "view": "requested"},
        {"q": row["target_title"]},
    ):
        matched = client.get(inbox, params=filters)
        assert matched.status_code == 200, matched.text
        assert [entry["id"] for entry in matched.json()["items"]] == [row["id"]]
    assert client.get(inbox, params={"q": "%"}).json()["total"] == 0
    assert client.get(inbox, params={"kind": "formal_approval"}).json()["total"] == 0

    replay = client.post(url, json=data)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == row["id"]
    assert db.scalar(select(func.count()).select_from(ArtifactRelease)) == 1
    conflict = client.post(url, json={**data, "note": "入力変更"})
    assert conflict.status_code == 409, conflict.text

    authorize_as(client, reviewer)
    assigned = client.get(f"{project_url}/requirement-workflow?view=assigned")
    assert assigned.status_code == 200, assigned.text
    assert [item["id"] for item in assigned.json()["items"]] == [row["id"]]
    content = client.get(
        f"{project_url}/requirement-workflow/{row['id']}/content?page_size=1"
    )
    assert content.status_code == 200, content.text
    assert content.json()["total"] == 2
    node = content.json()["items"][0]
    assert node["id"] == row["target_node_id"] and node["parent_id"] is None
    cursor = content.json()["next_cursor"]
    remaining = client.get(
        f"{project_url}/requirement-workflow/{row['id']}/content",
        params={"cursor": cursor, "page_size": 1},
    )
    assert remaining.status_code == 200, remaining.text
    assert [entry["node_kind"] for entry in remaining.json()["items"]] == [
        "requirement"
    ]
    assert remaining.json()["next_cursor"] is None
    linked_node = remaining.json()["items"][0]
    content_path = f"{project_url}/requirement-workflow/{row['id']}/content"
    assert client.get(content_path, params={"node_id": linked_node["id"]}).json() == {
        "release_id": row["release_id"],
        "items": [linked_node],
        "total": 1,
        "next_cursor": None,
    }
    assert client.get(content_path, params={"node_id": uuid4()}).status_code == 404
    assert (
        client.get(
            content_path, params={"node_id": node["id"], "cursor": cursor}
        ).status_code
        == 400
    )
    mine = client.get(f"{project_url}/requirement-workflow?view=requested")
    assert mine.json()["total"] == 0


def test_home_review_assignments_span_projects_and_respect_membership(
    client,
    db,
    review_case,
    create_test_project,
    assign_project_role,
    create_test_requirement_document,
    create_test_requirement_section,
):
    """複数案件の依頼を表示し、脱退した案件は件数にも含めない。"""
    first_url, first_data = _prepare(client, review_case)
    first = client.post(first_url, json=first_data).json()
    reviewer = review_case[-1]
    issuer = db.get(User, first["requester_id"])
    project = create_test_project(name="第二プロジェクト")
    assign_project_role(user=issuer, project=project, role_key="project_admin")
    membership = assign_project_role(user=reviewer, project=project, role_key="member")
    document = create_test_requirement_document(project=project)
    section = create_test_requirement_section(document=document)
    new_case = (f"/projects/{project.id}", document, section, None, reviewer)
    second_url, second_data = _prepare(client, new_case, count=0)
    second = client.post(second_url, json=second_data).json()

    path = "/home/requirement-work"
    assert client.get(path).json()["total"] == 0
    authorize_as(client, reviewer)
    page = client.get(path, params={"page_size": 1})
    assert page.status_code == 200, page.text
    assert page.headers["cache-control"] == "private, no-store"
    assert page.json()["total"] == 2
    assert page.json()["items"][0]["id"] == second["id"]
    assert page.json()["items"][0]["project_name"] == project.name
    other = client.get(path, params={"page_size": 1, "page": 2})
    assert other.json()["items"][0]["id"] == first["id"]

    membership.deleted_at = datetime.now(UTC)
    db.commit()
    visible = client.get(path).json()
    assert visible["total"] == 1
    assert [entry["id"] for entry in visible["items"]] == [first["id"]]


def test_document_review_includes_requirements_from_deleted_section(
    client, db, review_case
):
    """章が削除されても、有効な要件は定義書全体の対象に残る。"""
    project_url, document, section, requirement, reviewer = review_case
    section.deleted_at = datetime.now(UTC)
    db.commit()
    url = f"{project_url}/requirement-documents/{document.id}/review-requests"
    data = {"document_version": document.version, "participant_ids": [reviewer.id]}
    preview = client.post(url + "/preview", json=data)
    assert preview.status_code == 200, preview.text
    assert preview.json()["requirement_count"] == 1
    response = client.post(
        url,
        json={
            **data,
            "request_key": str(uuid4()),
            "expected_digest": preview.json()["confirmation_digest"],
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    content = client.get(f"{project_url}/requirement-workflow/{created['id']}/content")
    assert content.status_code == 200, content.text
    node = next(
        item for item in content.json()["items"] if item["node_kind"] == "requirement"
    )
    assert node["source_id"] == requirement.id
    assert node["parent_id"] == created["target_node_id"]


def test_edit_after_review_preview_requires_confirmation(client, db, review_case):
    """定義書の版番号が同じでも、章本文が変われば古い依頼を発行しない。"""
    _, _, section, _, _ = review_case
    url, data = _prepare(client, review_case)
    section.content = "別の担当者が保存した本文"
    section.version += 1
    db.commit()

    response = client.post(url, json=data)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "VERSION_CONFLICT"
    assert db.scalar(select(func.count()).select_from(ArtifactRelease)) == 0
    assert db.scalar(select(func.count()).select_from(RequirementWorkflowRequest)) == 0


def test_review_creation_failure_leaves_no_release(
    client, db, review_case, monkeypatch
):
    """依頼を保存できなかった処理の固定版を残さない。"""
    url, data = _prepare(client, review_case)

    def fail(*args, **kwargs):
        raise BadRequestError()

    monkeypatch.setattr(service.repository, "create", fail)
    response = client.post(url, json=data)
    assert response.status_code == 400, response.text
    assert db.scalar(select(func.count()).select_from(ArtifactRelease)) == 0
    assert db.scalar(select(func.count()).select_from(RequirementWorkflowRequest)) == 0


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_review_dispatch_keeps_distinct_version_numbers(
    client, db, review_case, monkeypatch, same_key
):
    """再送は1件に収束し、別の依頼には重複しない版番号を割り当てる。"""
    url, data = _prepare(client, review_case)
    barrier = Barrier(2)
    attempts = local()
    original = service.releases.builder.build

    def build(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        if not getattr(attempts, "seen", False):
            attempts.seen = True
            barrier.wait(timeout=15)
        return snapshot

    monkeypatch.setattr(service.releases.builder, "build", build)
    cookies, headers = dict(client.cookies), dict(client.headers)

    def send(_):
        payload = data if same_key else {**data, "request_key": str(uuid4())}
        with TestClient(create_app(), cookies=cookies, headers=headers) as peer:
            response = peer.post(url, json=payload)
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as worker:
        results = list(worker.map(send, range(2)))
    expected_count = 1 if same_key else 2
    assert sorted(code for code, _ in results) == [200 if same_key else 201, 201]
    assert {row["version_label"] for _, row in results} == {
        f"R-{number:03d}" for number in range(1, expected_count + 1)
    }
    assert len({row["id"] for _, row in results}) == expected_count
    assert (
        db.scalar(select(func.count()).select_from(ArtifactRelease)) == expected_count
    )
    assert (
        db.scalar(select(func.count()).select_from(RequirementWorkflowRequest))
        == expected_count
    )


def test_review_blocks_bulk_confirmation_and_preserves_resolution_history(
    client,
    db,
    review_case,
):
    """修正対象は一括確認から外れ、修正者の返信と指摘者の解決が履歴に残る。"""
    project_url, document, section, first, reviewer = review_case
    other = Requirement(
        document_id=document.id,
        section_id=section.id,
        requirement_code="REV-002",
        requirement_type="functional",
        title="別の要件",
    )
    db.add(other)
    db.commit()
    url, data = _prepare(client, review_case, count=2)
    created = client.post(url, json=data)
    assert created.status_code == 201, created.text
    request = created.json()
    base = f"{project_url}/requirement-workflow/{request['id']}"
    body = client.get(base + "/content").json()
    nodes = {
        node["source_id"]: node["id"]
        for node in body["items"]
        if node["node_kind"] == "requirement"
    }
    one, two = nodes[first.id], nodes[other.id]
    issuer = db.get(User, request["requester_id"])
    assert issuer is not None
    protected = client.put(
        base + f"/review-items/{one}", json={"version": 1, "confirmed": True}
    )
    assert protected.status_code == 403, protected.text

    authorize_as(client, reviewer)
    incomplete = client.post(base + "/review-result", json={"version": 1})
    assert incomplete.status_code == 409, incomplete.text
    verified = client.put(
        base + f"/review-items/{one}", json={"version": 1, "confirmed": True}
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["confirmed_count"] == 1
    invalid_anchor = client.post(
        base + "/review-threads",
        json={
            "version": 2,
            "node_id": one,
            "kind": "correction",
            "body": "定義を修正してください",
            "anchor": {"field": "title", "quote": "違う文字", "quote_start": 0},
        },
    )
    assert invalid_anchor.status_code == 400, invalid_anchor.text
    created_thread = client.post(
        base + "/review-threads",
        json={
            "version": 2,
            "node_id": one,
            "kind": "correction",
            "body": "定義を修正してください",
            "anchor": {"field": "title", "quote": "固定内容", "quote_start": 0},
        },
    )
    assert created_thread.status_code == 201, created_thread.text
    thread = created_thread.json()
    assert thread["status"] == "open" and thread["author_id"] == reviewer.id
    blocked = client.put(
        base + f"/review-items/{one}", json={"version": 3, "confirmed": True}
    )
    assert blocked.status_code == 409, blocked.text

    bulk = client.post(base + "/review-confirm-remaining", json={"version": 3})
    assert bulk.status_code == 200, bulk.text
    assert (
        bulk.json()["confirmed_count"] == 1
        and bulk.json()["changes_requested_count"] == 1
    )
    progress = client.get(
        base + "/review-progress", params=[("node_ids", one), ("node_ids", two)]
    )
    assert progress.json()["can_discuss"] is True
    assert {item["node_id"]: item["status"] for item in progress.json()["items"]} == {
        one: "changes_requested",
        two: "confirmed",
    }
    target_threads = client.get(base + "/review-threads", params={"node_id": one})
    assert [item["id"] for item in target_threads.json()["items"]] == [thread["id"]]
    other_threads = client.get(base + "/review-threads", params={"node_id": two})
    assert other_threads.json()["total"] == 0
    item_states = client.get(
        f"{project_url}/requirement-workflow/item-states",
        params={"requirement_ids": [first.id, other.id]},
    )
    assert item_states.status_code == 200, item_states.text
    assert {row["requirement_id"]: row["review"] for row in item_states.json()} == {
        first.id: "changes_requested",
        other.id: "confirmed",
    }
    missing_threads = client.get(base + "/review-threads", params={"node_id": uuid4()})
    assert missing_threads.status_code == 404
    management = f"{project_url}/requirement-documents/{document.id}/review-management"
    db.add_all(
        [
            RequirementOpenIssue(
                document_id=document.id,
                issue_code="ISSUE-OPEN",
                title="回答を待っている",
            ),
            RequirementOpenIssue(
                document_id=document.id,
                issue_code="ISSUE-DONE",
                title="合意済みの論点",
                status="resolved",
            ),
        ]
    )
    db.commit()
    summary = client.get(management, params={"resolved": "false"})
    assert summary.status_code == 200, summary.text
    data = summary.json()
    assert data["active_review_count"] == 1
    assert data["current_review"]["id"] == request["id"]
    assert data["current_review"]["participants"][0]["status"] == "pending"
    assert data["formal_approval"] is None
    assert (data["open_issue_count"], data["resolved_issue_count"]) == (1, 1)
    assert data["correction_total"] == data["open_correction_count"] == 1
    assert data["corrections"][0]["id"] == thread["id"]
    assert data["corrections"][0]["node_id"] == one
    for resolved, code in (("false", "ISSUE-OPEN"), ("true", "ISSUE-DONE")):
        issues = client.get(
            f"{project_url}/open-issues",
            params={"document_id": document.id, "resolved": resolved},
        )
        assert issues.status_code == 200, issues.text
        assert [item["issue_code"] for item in issues.json()["items"]] == [code]
    submitted = client.post(
        base + "/review-result", json={"version": 4, "note": "1件の修正をお願いします"}
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "changes_requested"
    assert submitted.json()["participants"][0]["responded_at"]
    home_work = "/home/requirement-work"
    assert client.get(home_work).json()["total"] == 0

    authorize_as(client, issuer)
    item = client.get(home_work).json()["items"][0]
    assert (item["id"], item["node_id"], item["kind"], item["status"]) == (
        thread["id"],
        one,
        "correction",
        "open",
    )
    thread_url = base + f"/review-threads/{thread['id']}"
    start = client.post(thread_url + "/actions", json={"version": 1, "action": "start"})
    assert start.status_code == 200, start.text
    answer = client.post(
        thread_url + "/actions",
        json={
            "version": 2,
            "action": "respond",
            "body": "記述を具体化しました",
        },
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "awaiting_confirmation"
    assert client.get(home_work).json()["total"] == 0
    illegal_close = client.post(
        thread_url + "/actions", json={"version": 3, "action": "resolve"}
    )
    assert illegal_close.status_code == 403, illegal_close.text
    no_reason = client.post(
        thread_url + "/actions", json={"version": 3, "action": "force_resolve"}
    )
    assert no_reason.status_code == 422, no_reason.text

    authorize_as(client, reviewer)
    assert client.get(home_work).json()["items"][0]["status"] == "awaiting_confirmation"
    confirm = client.post(
        thread_url + "/actions", json={"version": 3, "action": "resolve"}
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["status"] == "resolved"
    assert client.get(base).json()["status"] == "changes_requested"
    history = client.get(thread_url).json()["events"]
    assert [event["action"] for event in history] == ["start", "respond", "resolve"]
    assert history[-1]["actor_id"] == reviewer.id
    assert client.get(base + "/review-progress").json()["open_correction_count"] == 0
    assert client.get(home_work).json()["total"] == 0
    resolved_summary = client.get(management, params={"resolved": "true"}).json()
    assert resolved_summary["open_correction_count"] == 0
    assert resolved_summary["resolved_correction_count"] == 1
    assert resolved_summary["corrections"][0]["status"] == "resolved"
    assert resolved_summary["active_review_count"] == 0


def test_review_waits_for_each_participant_and_keeps_discussion_nonblocking(
    client, db, review_case
):
    """複数担当者の提出を待ち、通常コメントを修正依頼として扱わない。"""
    project_url, _, _, _, reviewer = review_case
    issuer_id = client.get("/auth/me").json()["id"]
    url, data = _prepare(client, review_case, participant_ids=[reviewer.id, issuer_id])
    request = client.post(url, json=data).json()
    base = f"{project_url}/requirement-workflow/{request['id']}"
    authorize_as(client, reviewer)
    comment = client.post(
        base + "/review-threads",
        json={
            "version": 1,
            "node_id": request["target_node_id"],
            "kind": "comment",
            "body": "用語の補足",
        },
    )
    assert comment.status_code == 201, comment.text
    sent = client.post(
        base + "/review-result", json={"version": 2, "confirm_remaining": True}
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "requested"
    assert client.get(base + "/review-progress").json()["open_correction_count"] == 0
    after_submit = client.post(base + "/review-result", json={"version": 3})
    assert after_submit.status_code == 409, after_submit.text
    issuer = db.get(User, issuer_id)
    assert issuer is not None
    authorize_as(client, issuer)
    unknown = client.put(
        base + f"/review-items/{UUID(int=1)}", json={"version": 3, "confirmed": True}
    )
    assert unknown.status_code == 404, unknown.text
    complete = client.post(
        base + "/review-result", json={"version": 3, "confirm_remaining": True}
    )
    assert complete.status_code == 200, complete.text
    assert complete.json()["status"] == "completed"
    assert {row["status"] for row in complete.json()["participants"]} == {"confirmed"}


def _formal_candidates(db, document, create_test_user, assign_project_role):
    from app.models.project import Project

    project = db.get(Project, document.project_id)
    assert project is not None
    approvers = [
        create_test_user(email=f"formal-approver-{index}@example.com")
        for index in (1, 2)
    ]
    for approver in approvers:
        assign_project_role(user=approver, project=project, role_key="project_admin")
    return project, approvers


def _formal_payload(client, path, data):
    preview = client.post(path + "/preview", json=data)
    assert preview.status_code == 200, preview.text
    result = preview.json()
    return result, {
        **data,
        "expected_digest": result["confirmation_digest"],
        "request_key": str(uuid4()),
        "confirmed_content": True,
    }


def _decision_payload(client, path, result="approved", **fields):
    preview = client.get(path)
    assert preview.status_code == 200, preview.text
    assert preview.json()["can_decide"]
    return {
        "operation_key": str(uuid4()),
        "decision": result,
        "version": preview.json()["request"]["version"],
        "expected_release_digest": preview.json()["release_digest"],
        "confirmed_content": True,
        **fields,
    }


def test_formal_approval_requires_matching_review_and_all_approvers(
    client, db, review_case, create_test_user, assign_project_role
):
    """固定本文と全員の判断を開発基準に確定し、後から書き換えない。"""
    from sqlalchemy import update
    from sqlalchemy.exc import DBAPIError

    from app.models.requirement_workflow import RequirementWorkflowParticipant

    project_url, document, section, requirement, reviewer = review_case
    issuer = db.get(User, client.get("/auth/me").json()["id"])
    assert issuer is not None
    _, approvers = _formal_candidates(
        db, document, create_test_user, assign_project_role
    )
    recipient_url = f"{project_url}/requirement-workflow/formal-approval-recipients"
    candidates = client.get(recipient_url)
    assert candidates.status_code == 200, candidates.text
    assert {candidate["id"] for candidate in candidates.json()} == {
        user.id for user in approvers
    }

    base = f"{project_url}/requirement-documents/{document.id}"
    path = base + "/formal-approval-requests"
    data = {
        "document_version": document.version,
        "participant_ids": [user.id for user in approvers],
    }
    preview, payload = _formal_payload(client, path, data)
    assert preview["review_required"] and not preview["can_request"]
    assert client.post(path, json=payload).status_code == 409
    assert (
        client.post(
            path + "/preview", json={**data, "scope_kind": "requirement"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            path + "/preview", json={**data, "participant_ids": [reviewer.id]}
        ).status_code
        == 400
    )

    review_data = {
        "document_version": document.version,
        "participant_ids": [reviewer.id],
    }
    review_path = base + "/review-requests"
    review_preview = client.post(review_path + "/preview", json=review_data)
    assert review_preview.status_code == 200, review_preview.text
    requested = client.post(
        review_path,
        json={
            **review_data,
            "request_key": str(uuid4()),
            "expected_digest": review_preview.json()["confirmation_digest"],
        },
    )
    assert requested.status_code == 201, requested.text
    review_id = requested.json()["id"]
    workflow_base = f"{project_url}/requirement-workflow"
    item_states = workflow_base + "/item-states"

    def read_item_states(*identities):
        result = client.get(item_states, params={"requirement_ids": list(identities)})
        assert result.status_code == 200, result.text
        return {item["requirement_id"]: item for item in result.json()}

    pending = read_item_states(requirement.id)[requirement.id]
    assert (pending["authoring"], pending["review"], pending["approval"]) == (
        "draft",
        "not_reviewed",
        "not_approved",
    )
    assert pending["review_request_id"] == review_id
    assert pending["review_item_node_id"]
    content = client.get(
        workflow_base + f"/{review_id}/content",
        params={"node_id": pending["review_item_node_id"]},
    )
    assert content.status_code == 200, content.text
    assert [
        (node["node_kind"], node["source_id"]) for node in content.json()["items"]
    ] == [("requirement", requirement.id)]
    authorize_as(client, reviewer)
    result = client.post(
        workflow_base + f"/{review_id}/review-result",
        json={"version": 1, "confirm_remaining": True},
    )
    assert result.status_code == 200, result.text

    authorize_as(client, issuer)
    preview, payload = _formal_payload(client, path, data)
    assert preview["can_request"] and preview["reviewed_request_id"] == review_id
    issue = RequirementOpenIssue(
        document_id=document.id,
        issue_code="ISSUE-OPTIONAL",
        title="開発後に追記する",
        blocks_approval=False,
    )
    db.add(issue)
    db.commit()

    issued = client.post(path, json=payload)
    assert issued.status_code == 201, issued.text
    request = issued.json()
    assert request["kind"] == "formal_approval" and request["scope_id"] == document.id
    assert request["version_label"] == "A-002"
    assert request["approval_basis"]["reviewed_request_id"] == review_id
    management = client.get(base + "/review-management")
    assert management.status_code == 200, management.text
    assert management.json()["formal_approval"]["id"] == request["id"]
    assert management.json()["current_review"]["status"] == "completed"
    assert client.post(path, json=payload).json()["id"] == request["id"]
    repeat = client.post(path, json={**payload, "request_key": str(uuid4())})
    assert repeat.status_code == 409, repeat.text

    full = workflow_base + f"/{request['id']}"
    decision_url = full + "/formal-approval-decision"
    authorize_as(client, approvers[0])
    work = client.get("/home/requirement-work").json()["items"][0]
    assert (work["id"], work["kind"], work["status"]) == (
        request["id"],
        "formal_approval",
        "pending",
    )
    first_data = _decision_payload(client, decision_url)
    first = client.post(decision_url, json=first_data)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "requested"
    assert client.get("/home/requirement-work").json()["total"] == 0
    assert client.get(base + "/formal-approval-baseline").json() is None
    again = client.post(decision_url, json=first_data)
    assert again.status_code == 200 and again.json()["version"] == 2
    tampered = client.post(decision_url, json={**first_data, "reason": "後から追加"})
    assert tampered.status_code == 409

    authorize_as(client, reviewer)
    assert client.post(decision_url, json=first_data).status_code == 403
    authorize_as(client, approvers[1])
    second_data = _decision_payload(client, decision_url)
    stale = client.post(decision_url, json={**second_data, "version": 1})
    assert stale.status_code == 409, stale.text
    wrong_release = client.post(
        decision_url, json={**second_data, "expected_release_digest": "0" * 64}
    )
    assert wrong_release.status_code == 409, wrong_release.text
    second = client.post(decision_url, json=second_data)
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "approved" and second.json()["finalized_at"]
    assert second.json()["version_label"] == request["version_label"]
    assert [row["status"] for row in second.json()["participants"]] == [
        "approved",
        "approved",
    ]
    assert client.get(base + "/formal-approval-baseline").json()["id"] == request["id"]
    current = read_item_states(requirement.id, 999999)[requirement.id]
    assert (current["authoring"], current["review"], current["approval"]) == (
        "draft",
        "confirmed",
        "approved",
    )
    assert current["approved_request_id"] == request["id"]
    assert current["changed_fields"] == []
    comparison = base + "/baseline-comparison"
    identical = client.get(comparison)
    assert identical.status_code == 200, identical.text
    assert identical.json()["total"] == 0

    section.content = "承認後に編集した章"
    requirement.title = "承認後に編集した要件"
    db.add(
        RequirementDetail(
            requirement_id=requirement.id,
            detail_type="functional",
            detail_json={"content": "承認後に追加した実現内容"},
        )
    )
    added = Requirement(
        document_id=document.id,
        requirement_code="REV-NEW",
        requirement_type="functional",
        title="承認後の追加要件",
    )
    db.add(added)
    db.commit()
    states = read_item_states(requirement.id, added.id)
    assert (states[requirement.id]["review"], states[requirement.id]["approval"]) == (
        "changed",
        "changed",
    )
    assert set(states[requirement.id]["changed_fields"]) == {"title", "details"}
    assert (states[added.id]["review"], states[added.id]["approval"]) == (
        "not_reviewed",
        "not_approved",
    )
    difference = client.get(comparison)
    assert difference.status_code == 200, difference.text
    difference_data = difference.json()
    assert difference_data["baseline_id"] == request["id"]
    assert (difference_data["baseline_count"], difference_data["current_count"]) == (
        1,
        2,
    )
    assert (
        difference_data["requirement_added"],
        difference_data["requirement_changed"],
    ) == (1, 1)
    assert difference_data["requirement_removed"] == 0
    assert {
        (item["kind"], item["id"], item["change"]): set(item["fields"])
        for item in difference_data["items"]
    } == {
        ("section", section.id, "changed"): {"content"},
        ("requirement", requirement.id, "changed"): {"title", "details"},
        ("requirement", added.id, "added"): set(),
    }
    second_page = client.get(comparison, params={"page": 2, "page_size": 2})
    assert second_page.json()["total"] == 3
    assert len(second_page.json()["items"]) == 1
    fixed = client.get(full + "/content")
    assert fixed.status_code == 200, fixed.text
    titles = [item["payload"].get("title") for item in fixed.json()["items"]]
    assert "固定内容を読める" in titles and "承認後に編集した要件" not in titles

    with pytest.raises(DBAPIError):
        db.execute(
            update(RequirementWorkflowParticipant)
            .where(RequirementWorkflowParticipant.request_id == UUID(request["id"]))
            .values(response_note="事後修正")
        )
    db.rollback()
    assert client.get(base + "/formal-approval-baseline").json()["id"] == request["id"]
    authorize_as(client, issuer)
    new_preview, _ = _formal_payload(client, path, data)
    assert new_preview["previous_baseline_id"] == request["id"]
    assert not new_preview["can_request"] and new_preview["reviewed_request_id"] is None

    def confirm_changed_scope(kind, source_id):
        review_input = {**review_data, "scope_kind": kind, "scope_id": source_id}
        confirmed = client.post(review_path + "/preview", json=review_input).json()
        response = client.post(
            review_path,
            json={
                **review_input,
                "request_key": str(uuid4()),
                "expected_digest": confirmed["confirmation_digest"],
            },
        )
        assert response.status_code == 201, response.text
        review_request = response.json()
        authorize_as(client, reviewer)
        result = client.post(
            f"{workflow_base}/{review_request['id']}/review-result",
            json={"version": 1, "confirm_remaining": True},
        )
        assert result.status_code == 200, result.text
        authorize_as(client, issuer)
        return review_request["id"]

    section_reviewed = confirm_changed_scope("section", section.id)
    assert not _formal_payload(client, path, data)[0]["can_request"]
    added_reviewed = confirm_changed_scope("requirement", added.id)
    verified, verified_payload = _formal_payload(client, path, data)
    assert verified["can_request"]
    assert set(verified["reviewed_request_ids"]) == {section_reviewed, added_reviewed}
    requirement.acceptance_criteria = "再確認の後で変更しました"
    db.commit()
    assert client.post(path, json=verified_payload).status_code == 409
    assert not _formal_payload(client, path, data)[0]["can_request"]
    requirement.acceptance_criteria = None
    db.commit()
    _, confirmed_payload = _formal_payload(client, path, data)
    next_request = client.post(path, json=confirmed_payload)
    assert next_request.status_code == 201, next_request.text
    assert next_request.json()["scope_kind"] == "requirement_document"
    assert set(next_request.json()["approval_basis"]["reviewed_request_ids"]) == {
        section_reviewed,
        added_reviewed,
    }
    assert client.get(base + "/formal-approval-baseline").json()["id"] == request["id"]


def test_formal_approval_revalidates_blockers_and_records_exception(
    client, db, review_case, create_test_user, assign_project_role
):
    """申請直前の変更、例外の責任、差し戻しによる基準版の保全を確認する。"""
    project_url, document, _, _, _ = review_case
    issuer = db.get(User, client.get("/auth/me").json()["id"])
    assert issuer is not None
    project, approvers = _formal_candidates(
        db, document, create_test_user, assign_project_role
    )
    changed = client.patch(
        project_url,
        json={"version": project.version, "requirement_review_required": False},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["requirement_review_required"] is False

    base = f"{project_url}/requirement-documents/{document.id}"
    path = base + "/formal-approval-requests"
    data = {"document_version": document.version, "participant_ids": [approvers[0].id]}
    preview, payload = _formal_payload(client, path, data)
    assert preview["can_request"] and not preview["review_required"]
    blocker = RequirementOpenIssue(
        document_id=document.id, issue_code="ISSUE-REQUIRED", title="移行方針の確認"
    )
    db.add(blocker)
    db.commit()
    stale = client.post(path, json=payload)
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
    preview, blocked_payload = _formal_payload(client, path, data)
    assert not preview["can_request"] and preview["blockers"] == [
        {"kind": "open_issue", "id": str(blocker.id), "title": blocker.title}
    ]
    assert client.post(path, json=blocked_payload).status_code == 409
    exception = {
        "kind": "open_issue",
        "id": str(blocker.id),
        "reason": "並行して確認し担当者が追記する",
        "owner_id": issuer.id,
        "due_on": "2026-12-05",
    }
    preview, allowed = _formal_payload(
        client, path, {**data, "exceptions": [exception]}
    )
    assert preview["can_request"]
    issued = client.post(path, json=allowed)
    assert issued.status_code == 201, issued.text
    assert issued.json()["approval_basis"]["exceptions"] == [exception]

    new_blocker = RequirementOpenIssue(
        document_id=document.id, issue_code="ISSUE-NEW", title="承認前に発覚した障害"
    )
    db.add(new_blocker)
    db.commit()
    authorize_as(client, approvers[0])
    workflow_path = f"{project_url}/requirement-workflow/{issued.json()['id']}"
    decision_url = workflow_path + "/formal-approval-decision"
    rejected = client.post(decision_url, json=_decision_payload(client, decision_url))
    assert rejected.status_code == 409, rejected.text
    new_blocker.status = "resolved"
    blocker.version += 1
    blocker.description = "申請後に判明した条件を追記"
    db.commit()
    modified = client.post(decision_url, json=_decision_payload(client, decision_url))
    assert modified.status_code == 409, modified.text
    returned = _decision_payload(client, decision_url, "returned")
    assert client.post(decision_url, json=returned).status_code == 422
    result = client.post(
        decision_url, json={**returned, "reason": "障害の扱いを決めてください"}
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "returned"
    assert (
        result.json()["participants"][0]["response_note"]
        == "障害の扱いを決めてください"
    )
    assert client.get(base + "/formal-approval-baseline").json() is None
