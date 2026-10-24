"""固定根拠・指定本人・現在有効性を実PostgreSQLで検証する。"""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from tests.helpers.auth import authorize_as
from tests.routers.test_requirement_verifications import context as context
from tests.routers.test_requirement_verifications import manual_row, update
from tests.routers.test_requirement_verifications import prepared as prepared
from tests.routers.test_verification_evidence import create as submit
from tests.routers.test_verification_evidence import evidence as evidence


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    for flag in ("preparation", "evidence", "decisions"):
        monkeypatch.setattr(settings, f"requirement_verification_{flag}_enabled", True)


@pytest.fixture
def verdict(client, evidence):
    c = evidence
    rows = c["plan"]["revision"]["checklist"]["rows"]
    response, _ = update(client, c, rows, confirm_coverage=True)
    assert response.status_code == 200, response.text
    c["plan"] = response.json()
    c["selection"]["plan_version"] = c["plan"]["current_version"]
    saved, _ = submit(client, c)
    c["verdicts"] = f"/projects/{c['project'].id}/requirement-verification-requests"
    c["verdict_input"] = dict(
        plan_id=c["plan"]["id"],
        plan_version=c["plan"]["current_version"],
        judge_id=c["user"].id,
        entries=[dict(set_id=saved["id"], entry_id=c["selection"]["entries"][0]["id"])],
    )
    return c


def request(client, c, **changes):
    data = {**c["verdict_input"], **changes}
    preview = client.post(c["verdicts"] + "/preview", json=data)
    assert preview.status_code == 200, preview.text
    payload = dict(
        data,
        operation_id=str(uuid4()),
        expected_preview_digest=preview.json()["preview_digest"],
    )
    response = client.post(c["verdicts"], json=payload)
    assert response.status_code == 201, response.text
    return response.json(), payload


def begin(client, c, row):
    url = c["verdicts"] + "/" + row["id"]
    current = client.get(url).json()
    response = client.post(
        url + "/review",
        json=dict(version=current["version"], review_version=current["review_version"]),
    )
    assert response.status_code == 200, response.text
    return url, response.json()


def decide(client, url, row, **values):
    data = {
        "version": row["version"],
        "review_version": row["review_version"],
        "operation_id": str(uuid4()),
        "result": "passed",
        "reason": "固定した要件の全条件を確認した",
        **values,
    }
    return client.post(url + "/decide", json=data), data


def test_self_verdict_idempotency_and_immutable_history(client, verdict, db):
    c = verdict
    row, data = request(client, c)
    same = client.post(c["verdicts"], json=data)
    assert same.status_code == 200 and same.json()["id"] == row["id"]
    assert (
        client.post(c["verdicts"], json={**data, "judge_id": 99999}).status_code == 409
    )
    url, review = begin(client, c, row)
    response, operation = decide(client, url, review)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (
        result["status"] == "decided"
        and result["result"] == result["validity"] == "passed"
    )
    assert client.post(url + "/decide", json=operation).status_code == 200
    assert (
        client.post(url + "/decide", json={**operation, "reason": "別内容"}).status_code
        == 409
    )
    events = client.get(url + "/events").json()["items"]
    assert [e["event"] for e in events] == ["requested", "decided"]
    assert events[1]["snapshot"]["decision"]["readiness"]["coverage_confirmed"]
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    for sql in (
        "UPDATE requirement_verification_requests SET judge_name='代理'",
        "UPDATE requirement_verification_events SET reason='改変'",
        "DELETE FROM requirement_verification_requests",
    ):
        with pytest.raises(DBAPIError):
            db.execute(text(sql))
        db.rollback()


@pytest.mark.parametrize(
    "role,can_prepare,can_judge",
    [
        ("project_admin", True, True),
        ("manager", True, True),
        ("member", False, True),
        ("viewer", False, False),
    ],
)
def test_roles_and_designated_person(
    client, verdict, create_test_user, assign_project_role, role, can_prepare, can_judge
):
    c = verdict
    other = create_test_user(email=f"{role}@example.com")
    assign_project_role(user=other, project=c["project"], role_key=role)
    candidates = client.get(c["verdicts"] + "/candidates").json()["items"]
    assert (other.id in {r["id"] for r in candidates}) == can_judge
    row, _ = request(client, c)
    authorize_as(client, other)
    detail = client.get(c["verdicts"] + "/" + row["id"])
    assert detail.status_code == 200, detail.text
    assert (
        not detail.json()["can_review"] and detail.json()["can_cancel"] == can_prepare
    )
    denied = client.post(
        c["verdicts"] + "/" + row["id"] + "/review",
        json=dict(version=1, review_version=0),
    )
    assert denied.status_code == 403


def test_unconfirmed_coverage_cannot_pass_but_can_fail(client, verdict):
    c = verdict
    rows = c["plan"]["revision"]["checklist"]["rows"]
    response, _ = update(client, c, rows)
    c["plan"] = response.json()
    c["selection"]["plan_version"] += 1
    c["selection"]["entries"][0]["id"] = str(uuid4())
    saved, _ = submit(client, c)
    c["verdict_input"].update(
        plan_version=c["selection"]["plan_version"],
        entries=[dict(set_id=saved["id"], entry_id=c["selection"]["entries"][0]["id"])],
    )
    row, _ = request(client, c)
    url, review = begin(client, c, row)
    response, _ = decide(client, url, review)
    assert response.status_code == 409
    current = client.get(url).json()
    response, _ = decide(
        client, url, current, result="failed", unresolved_rows=[rows[0]["id"]]
    )
    assert response.status_code == 200, response.text
    assert not response.json()["readiness"]["coverage_confirmed"]


def test_explicit_chain_cancel_withdraw_does_not_restore_old_pass(client, verdict):
    c = verdict
    first, _ = request(client, c)
    url, review = begin(client, c, first)
    passed, _ = decide(client, url, review)
    assert passed.status_code == 200, passed.text
    preview = client.post(c["verdicts"] + "/preview", json=c["verdict_input"]).json()
    payload = dict(
        c["verdict_input"],
        operation_id=str(uuid4()),
        expected_preview_digest=preview["preview_digest"],
    )
    assert client.post(c["verdicts"], json=payload).status_code == 409
    second, _ = request(
        client,
        c,
        previous_request_id=first["id"],
        reviewed_decision_id=first["id"],
        reason="新しい確認を依頼",
    )
    cancelled = client.post(
        c["verdicts"] + "/" + second["id"] + "/cancel",
        json=dict(version=1, operation_id=str(uuid4()), reason="対象を見直す"),
    )
    assert cancelled.status_code == 200, cancelled.text
    aggregate = f"/projects/{c['project'].id}/requirement-verdicts"
    assert (
        client.get(aggregate, params={"requirement_ids": c["req"].id}).json()["items"][
            0
        ]["state"]
        == "cancelled"
    )
    third, _ = request(
        client,
        c,
        previous_request_id=second["id"],
        reviewed_decision_id=first["id"],
        reason="取消した依頼を作り直す",
    )
    url3, review3 = begin(client, c, third)
    passed3, _ = decide(client, url3, review3)
    assert passed3.status_code == 200, passed3.text
    withdrawn = client.post(
        url3 + "/withdraw",
        json=dict(version=2, operation_id=str(uuid4()), reason="後の疑義により撤回"),
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert (
        withdrawn.json()["result"] == "passed"
        and withdrawn.json()["status"] == "withdrawn"
    )
    assert (
        client.get(aggregate, params={"requirement_ids": c["req"].id}).json()["items"][
            0
        ]["state"]
        == "withdrawn"
    )


def test_later_manual_ng_then_ok_never_restores_pass(client, verdict):
    c = verdict
    row, _ = request(client, c)
    url, review = begin(client, c, row)
    passed, _ = decide(client, url, review)
    assert passed.status_code == 200, passed.text
    for result in ("failed", "passed"):
        c["selection"]["entries"][0].update(
            id=str(uuid4()),
            result=result,
            actual_result="後日の再確認",
            reported_at=datetime.now(UTC).isoformat(),
        )
        submit(client, c)
    detail = client.get(url).json()
    assert (
        detail["result"] == "passed" and detail["validity"] == "requires_reassessment"
    )
    assert detail["comparison"]["adverse"] == "found"


def test_session_bound_review_and_qualification_epoch(client, verdict, db):
    from app.models.project import ProjectMember

    c = verdict
    row, _ = request(client, c)
    url, review = begin(client, c, row)
    authorize_as(client, c["user"])
    response, _ = decide(client, url, review)
    assert response.status_code == 409
    member = db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == c["project"].id,
            ProjectMember.user_id == c["user"].id,
        )
    )
    member.version += 1
    db.commit()
    detail = client.get(url).json()
    assert not detail["can_review"] and detail["can_cancel"]
    assert (
        client.post(
            url + "/review",
            json=dict(version=1, review_version=review["review_version"]),
        ).status_code
        == 409
    )


def test_all_na_needs_explicit_judge_confirmation_and_is_not_passed(client, evidence):
    c = evidence
    condition = manual_row(
        method="not_applicable",
        manual_plan="",
        not_applicable_reason="今回の対象範囲外",
    )
    response, _ = update(client, c, [condition], confirm_coverage=True)
    assert response.status_code == 200, response.text
    c["plan"] = response.json()
    c["selection"] = dict(
        plan_version=3,
        entries=[
            dict(
                id=str(uuid4()),
                row_id=condition["id"],
                kind="not_applicable",
                reason="要件本文の対象範囲と照合して適用外と確認",
            )
        ],
    )
    saved, _ = submit(client, c)
    c["verdicts"] = f"/projects/{c['project'].id}/requirement-verification-requests"
    c["verdict_input"] = dict(
        plan_id=c["plan"]["id"],
        plan_version=3,
        judge_id=c["user"].id,
        entries=[dict(set_id=saved["id"], entry_id=c["selection"]["entries"][0]["id"])],
    )
    row, _ = request(client, c)
    url, review = begin(client, c, row)
    assert (
        review["readiness"]["not_applicable_eligible"]
        and not review["readiness"]["passed_eligible"]
    )
    denied, _ = decide(client, url, review, result="not_applicable")
    assert denied.status_code == 400, denied.text
    current = client.get(url).json()
    approved, _ = decide(
        client,
        url,
        current,
        result="not_applicable",
        confirmed_not_applicable_rows=[condition["id"]],
    )
    assert (
        approved.status_code == 200 and approved.json()["result"] == "not_applicable"
    ), approved.text


def test_fixed_old_requirement_needs_ack_and_never_becomes_current_pass(
    client, verdict, db
):
    c = verdict
    row, _ = request(client, c)
    c["req"].description = "現在の本文へ変更"
    c["req"].version += 1
    db.commit()
    url, review = begin(client, c, row)
    denied, _ = decide(client, url, review)
    assert denied.status_code == 409
    current = client.get(url).json()
    accepted, _ = decide(client, url, current, acknowledge_requirement_difference=True)
    assert accepted.status_code == 200, accepted.text
    assert (
        accepted.json()["result"] == "passed"
        and accepted.json()["validity"] == "requires_reassessment"
    )


def test_whole_inventory_and_ng_between_later_ok(client, evidence, db):
    from app.models.test_design import RequirementTestItem
    from tests.routers.test_test_designs import graph

    c = evidence
    root = f"/projects/{c['project'].id}/test-designs"
    design = client.post(root, json={"name": "判定の全対象"}).json()
    source = graph(design)
    design_url = root + f"/{design['id']}"
    assert client.put(design_url, json=source).status_code == 200
    item = source["items"][0]["id"]
    db.add(
        RequirementTestItem(
            requirement_id=c["req"].id, item_id=UUID(item), created_by=c["user"].id
        )
    )
    db.commit()
    condition = manual_row(
        method="test",
        manual_plan="",
        targets=[dict(item_id=item, pattern_scope="all_enabled")],
    )
    response, _ = update(client, c, [condition], confirm_coverage=True)
    assert response.status_code == 200, response.text
    c["plan"] = response.json()
    cases = [
        v
        for v in client.get(design_url + "/cases").json()
        if v["source"]["item"]["id"] == item
    ]
    assert len(cases) == 2
    for case in cases:
        response = client.patch(
            design_url + f"/cases/{case['id']}",
            json=dict(
                version=case["version"], status="passed", actual_result="確認済み"
            ),
        )
        assert response.status_code == 200, response.text
    candidates = client.get(
        c["detail"] + "/execution-candidates", params={"row_id": condition["id"]}
    ).json()["items"]
    c["selection"] = dict(
        plan_version=3,
        entries=[
            dict(
                id=str(uuid4()),
                row_id=condition["id"],
                kind="execution",
                execution_id=v["execution_id"],
                reconfirmed=True,
                reason="固定要件への対応を今再確認",
            )
            for v in candidates
        ],
    )
    saved, _ = submit(client, c)
    c["verdicts"] = f"/projects/{c['project'].id}/requirement-verification-requests"
    c["verdict_input"] = dict(
        plan_id=c["plan"]["id"],
        plan_version=3,
        judge_id=c["user"].id,
        entries=[
            dict(set_id=saved["id"], entry_id=e["id"])
            for e in c["selection"]["entries"]
        ],
    )
    partial = client.post(
        c["verdicts"] + "/preview",
        json={**c["verdict_input"], "entries": c["verdict_input"]["entries"][:1]},
    )
    assert partial.status_code == 200, partial.text
    assert not partial.json()["readiness"]["passed_eligible"]
    row, _ = request(client, c)
    url, review = begin(client, c, row)
    passed, _ = decide(client, url, review)
    assert passed.status_code == 200, passed.text
    for result in ("failed", "passed"):
        case = next(
            v
            for v in client.get(design_url + "/cases").json()
            if v["id"] == cases[0]["id"]
        )
        response = client.patch(
            design_url + f"/cases/{case['id']}",
            json=dict(
                version=case["version"], status=result, actual_result="後日の実行"
            ),
        )
        assert response.status_code == 200, response.text
    current = client.get(url).json()
    assert (
        current["result"] == "passed" and current["validity"] == "requires_reassessment"
    )
    aggregate = client.get(
        f"/projects/{c['project'].id}/requirement-verdicts",
        params={"requirement_ids": c["req"].id},
    )
    assert (
        aggregate.status_code == 200
        and aggregate.json()["items"][0]["state"] == "requires_reassessment"
    ), aggregate.text
    # 既存2ケースを持つ同一要件fixtureを、10/50の独立照合scopeへ拡張する。
    import json
    import time
    from pathlib import Path
    from types import SimpleNamespace

    from sqlalchemy import event

    from app.models.verification_verdict import VerificationRequest
    from app.routers.verification_verdicts import service

    stored = db.get(VerificationRequest, UUID(row["id"]))
    measurements = []
    for count in (10, 50):
        scopes = [
            SimpleNamespace(
                **{
                    **{
                        col.name: getattr(stored, col.name)
                        for col in VerificationRequest.__table__.c
                    },
                    "id": uuid4(),
                }
            )
            for _ in range(count)
        ]
        statements = []

        def statement(connection, cursor, sql, parameters, context, executemany):
            statements.append(sql)

        event.listen(db.get_bind(), "before_cursor_execute", statement)
        started = time.perf_counter()
        try:
            values = service.builder.compare_many(db, c["project"].id, scopes)
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", statement)
        assert all(v["adverse"] == "found" for v in values.values())
        measurements.append(
            dict(
                scopes=count,
                business_sql=len(statements),
                seconds=time.perf_counter() - started,
            )
        )
    assert measurements[0]["business_sql"] == measurements[1]["business_sql"] <= 20
    Path(".verify/v2").mkdir(parents=True, exist_ok=True)
    Path(".verify/v2/comparison-metrics.json").write_text(
        json.dumps(
            dict(
                fixture="same requirement, two cases, distinct comparison scopes",
                measurements=measurements,
            )
        )
    )


def test_file_io_outside_locks_and_cancelled_claim_cannot_finish(
    client, verdict, db, monkeypatch
):
    from app.routers.verification_evidence import service as evidence_service
    from app.routers.verification_verdicts import service
    from tests.routers.test_verification_evidence import attach

    c = verdict
    attach(db, c)

    class Storage:
        during: Callable[[], None] | None = None

        def copy(self, file, key):
            return dict(
                destination_version="dummy-v1",
                sha256="a" * 64,
                byte_size=file["byte_size"],
            )

        def verify(self, *args, **kwargs):
            if self.during is not None:
                self.during()

    storage = Storage()
    monkeypatch.setattr(evidence_service, "storage_factory", lambda: storage)
    saved, _ = submit(client, c)
    ready = client.post(
        c["evidence"] + "/" + saved["id"] + "/advance",
        json={"version": saved["version"]},
    )
    assert ready.status_code == 200 and ready.json()["status"] == "ready", ready.text
    c["verdict_input"]["entries"] = [
        dict(set_id=saved["id"], entry_id=c["selection"]["entries"][0]["id"])
    ]
    row, _ = request(client, c)
    url, review = begin(client, c, row)
    monkeypatch.setattr(service, "storage_factory", lambda: storage)

    def during():
        # 別接続の取消がIO中に完了すればrequest lockを保持していない。
        response = client.post(
            url + "/cancel",
            json=dict(version=1, operation_id=str(uuid4()), reason="確認中に取消"),
        )
        assert response.status_code == 200, response.text

    storage.during = during
    result = client.post(
        url + "/review/advance",
        json=dict(version=1, review_version=review["review_version"]),
    )
    assert result.status_code == 409, result.text
    current = client.get(url).json()
    assert current["status"] == "cancelled" and current["result"] is None


def test_flags_cross_project_and_denials_are_private(
    client, verdict, monkeypatch, create_test_project, assign_project_role
):
    c = verdict
    row, _ = request(client, c)
    other = create_test_project(project_code="OTHER", name="別案件")
    assign_project_role(user=c["user"], project=other, role_key="project_admin")
    denied = client.get(
        f"/projects/{other.id}/requirement-verification-requests/{row['id']}"
    )
    assert (
        denied.status_code == 404
        and denied.headers["cache-control"] == "private, no-store"
    )
    monkeypatch.setattr(settings, "requirement_verification_decisions_enabled", False)
    disabled = client.get(c["verdicts"])
    assert disabled.status_code == 200 and disabled.json()["state"] == "disabled"
    assert client.get(c["verdicts"] + "/" + row["id"]).status_code == 404


def test_file_proof_required_head_failure_and_source_removal(
    client, verdict, db, monkeypatch
):
    from app.core.exceptions import SharedFileUnavailableError
    from app.routers.verification_evidence import service as evidence_service
    from app.routers.verification_verdicts import service
    from tests.routers.test_verification_evidence import attach

    c = verdict
    original = attach(db, c)

    class Storage:
        bucket = "isolated"
        unavailable = False
        calls = 0

        @property
        def destination(self):
            return self

        def copy(self, file, key):
            return dict(
                destination_version="dummy-v1",
                sha256="a" * 64,
                byte_size=file["byte_size"],
            )

        def verify(self, *args, **kwargs):
            self.calls += 1

        def head_object(self, **kwargs):
            if self.unavailable:
                raise SharedFileUnavailableError()
            return {"VersionId": "dummy-v1", "ContentLength": 5}

    storage = Storage()
    monkeypatch.setattr(evidence_service, "storage_factory", lambda: storage)
    monkeypatch.setattr(service, "storage_factory", lambda: storage)
    saved, _ = submit(client, c)
    ready = client.post(
        c["evidence"] + "/" + saved["id"] + "/advance",
        json={"version": saved["version"]},
    )
    assert ready.status_code == 200 and ready.json()["status"] == "ready", ready.text
    c["verdict_input"]["entries"] = [
        dict(set_id=saved["id"], entry_id=c["selection"]["entries"][0]["id"])
    ]
    row, _ = request(client, c)
    url, review = begin(client, c, row)
    denied, _ = decide(client, url, review)
    assert denied.status_code == 409
    advanced = client.post(
        url + "/review/advance",
        json=dict(version=1, review_version=review["review_version"]),
    )
    assert advanced.status_code == 200 and advanced.json()["verified_files"] == 1, (
        advanced.text
    )
    assert (
        "preserved_key" not in advanced.text
        and "destination_version" not in advanced.text
    )
    storage.unavailable = True
    rejected, _ = decide(client, url, advanced.json())
    assert rejected.status_code == 503, rejected.text
    current = client.get(url).json()
    assert current["result"] is None and current["verified_files"] == 0
    storage.unavailable = False
    retry = client.post(
        url + "/review/advance",
        json=dict(version=1, review_version=current["review_version"]),
    )
    approved, _ = decide(client, url, retry.json())
    assert approved.status_code == 200 and approved.json()["result"] == "passed", (
        approved.text
    )
    original.deleted_at = datetime.now(UTC)
    db.commit()
    current = client.get(url).json()
    assert current["result"] == "passed" and current["validity"] == "not_evaluated"
    assert len(current["files"]) == 1


def test_current_mfa_and_expired_review_are_enforced(client, verdict, db, monkeypatch):
    from app.models.verification_verdict import VerificationRequest

    c = verdict
    row, _ = request(client, c)
    url, review = begin(client, c, row)
    monkeypatch.setattr(settings, "mfa_required", True)
    denied, _ = decide(client, url, review)
    assert denied.status_code == 403 and denied.json()["code"] == "MFA_REQUIRED", (
        denied.text
    )
    monkeypatch.setattr(settings, "mfa_required", False)
    saved = db.get(VerificationRequest, UUID(row["id"]))
    saved.review = {**saved.review, "expires_at": "2020-01-01T00:00:00+00:00"}
    saved.review_version += 1
    db.commit()
    current = client.get(url).json()
    denied, _ = decide(client, url, current)
    assert denied.status_code == 409 and current["result"] is None
