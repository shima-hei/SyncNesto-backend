"""現在内容への合意・権限・上限・同時変更を代表契約で検証する。"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from app.core.config import settings
from app.models.rbac import Permission, RolePermission
from app.models.requirement import RequirementDetail
from app.repositories.requirement_agreement import RequirementAgreementRepository
from app.services.requirement_agreement import RequirementAgreementService
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_releases import context as context
from tests.routers.test_fixed_approvals import approval_context as approval_context
from tests.routers.test_fixed_approvals import decide, request, scope_data
from tests.routers.test_guest_fixed_approvals import create_request as guest_request
from tests.routers.test_guest_fixed_approvals import decision as guest_decision
from tests.routers.test_guest_fixed_approvals import (
    guest_approval_context as guest_approval_context,
)
from tests.routers.test_guest_fixed_approvals import guest_login
from tests.routers.test_project_objectives import objective_setup as objective_setup
from tests.routers.test_publication_access import access_fixture as access_fixture
from tests.routers.test_requirement_trace import trace_setup as trace_setup


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    """新規読取flagだけを隔離DBで有効にする。"""
    monkeypatch.setattr(settings, "project_traceability_enabled", True)
    monkeypatch.setattr(settings, "project_requirement_agreements_enabled", True)


def read(client, project_id, ids):
    """表示対象だけをまとめて問い合わせる。"""
    return client.get(
        f"/projects/{project_id}/requirement-agreements",
        params=[("requirement_ids", rid) for rid in ids],
    )


def test_no_record_scope_validation_and_restricted(
    client, db, trace_setup, monkeypatch
):
    """0件/制限/無効と不正IDを区別し、制限時は対象IDも返さない。"""
    c = trace_setup()
    response = read(client, c.project.id, [c.req.id])
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    row = response.json()["items"][0]
    assert row["internal"]["state"] == row["external"]["state"] == "unagreed"
    assert read(client, c.project.id, [c.req.id, 999999]).status_code == 404
    assert read(client, c.project.id, [0]).status_code == 422
    assert read(client, c.project.id, [c.req.id] * 101).status_code == 422
    from app.models.project import ProjectMember

    role_id = db.scalar(
        select(ProjectMember.role_id).where(
            ProjectMember.project_id == c.project.id, ProjectMember.user_id == c.user.id
        )
    )
    permission = db.scalar(
        select(Permission.id).where(Permission.code == "test_plan:read")
    )
    db.execute(
        delete(RolePermission).where(
            RolePermission.role_id == role_id,
            RolePermission.permission_id == permission,
        )
    )
    db.commit()
    restricted = read(client, c.project.id, [c.req.id]).json()
    assert restricted["state"] == "restricted" and restricted["items"] == []
    monkeypatch.setattr(settings, "project_requirement_agreements_enabled", False)
    assert read(client, c.project.id, [c.req.id]).json()["state"] == "disabled"


def test_actual_fixed_digest_and_detail_only_change(client, db, approval_context):
    """既存固定scopeと同じdigestを作り、親version不変の詳細変更も検出する。"""
    c = approval_context
    row, _ = request(client, c)
    pending = read(client, c["project"].id, [c["req"].id])
    assert pending.status_code == 200, pending.text
    assert pending.json()["items"][0]["internal"]["state"] == "pending"
    authorize_as(client, c["approver"])
    decide(client, c, row)
    current = read(client, c["project"].id, [c["req"].id]).json()["items"][0]
    assert current["internal"]["state"] == "agreed"
    assert current["internal"]["approval_id"] == row["id"]
    assert current["internal"]["event_id"] is not None
    assert current["external"]["state"] == "unagreed"
    before_version = c["req"].version
    detail = db.scalar(
        select(RequirementDetail).where(RequirementDetail.requirement_id == c["req"].id)
    )
    detail.detail_json = {"rule": "詳細のみ改訂"}
    db.commit()
    changed = read(client, c["project"].id, [c["req"].id]).json()["items"][0]
    assert (
        changed["version"] == before_version
        and changed["internal"]["state"] == "changed"
    )


def test_cancelled_is_not_subject_change(client, approval_context):
    """同内容の取消を旧仕様のchangedへ落とさない。"""
    c = approval_context
    row, _ = request(client, c)
    response = client.post(
        f"{c['approvals']}/{row['id']}/cancel",
        json={
            "version": 1,
            "operation_id": "b8144eb3-519b-44e3-bfc3-d49594b6c459",
            "reason": "依頼を取り消す",
        },
    )
    assert response.status_code == 200, response.text
    result = read(client, c["project"].id, [c["req"].id]).json()["items"][0]
    assert result["internal"]["state"] == "cancelled"


def test_conflicting_decisions_do_not_choose_last(client, approval_context):
    """同じ固定対象への相反する本人判断を残す。"""
    c = approval_context
    for decision in ["approve", "reject"]:
        authorize_as(client, c["user"])
        row, _ = request(client, c)
        authorize_as(client, c["approver"])
        decide(client, c, row, decision=decision, comment="確認結果")
    result = read(client, c["project"].id, [c["req"].id]).json()["items"][0]
    assert result["internal"]["state"] == "conflicting"


def test_limits_and_concurrent_content_read(client, db, trace_setup, monkeypatch):
    """巨大本文は取得しない。同じ親版でも読取中に内容が変われば未集計。"""
    c = trace_setup()
    c.req.description = "大" * (400 * 1024)
    db.commit()
    result = read(client, c.project.id, [c.req.id]).json()["items"][0]
    assert result["internal"]["reason"] == "size_limit"
    c.req.description = "通常本文"
    db.commit()
    original = RequirementAgreementRepository.subjects
    count = 0

    def changing(self, db, project_id, ids):
        nonlocal count
        rows = original(self, db, project_id, ids)
        count += 1
        if count == 2:
            rows = [dict(r) for r in rows]
            rows[0]["subject"]["payload"]["description"] = "同時変更"
        return rows

    monkeypatch.setattr(RequirementAgreementRepository, "subjects", changing)
    assert (
        read(client, c.project.id, [c.req.id]).json()["items"][0]["internal"]["reason"]
        == "concurrent_change"
    )


@pytest.mark.no_db
@pytest.mark.parametrize(
    "histories,legacy,state",
    [
        ([], False, "unagreed"),
        ([], True, "legacy_unverified"),
        ([("requested", False, True)], False, "changed"),
        ([("approved", True, False)], False, "not_evaluated"),
    ],
)
def test_classification_boundaries(histories, legacy, state):
    """本文のない最小代表集合で旧記録・対象違い・証跡欠損を区別する。"""
    rows = [
        dict(
            status=status,
            same=same,
            valid=valid,
            approval_id=UUID(int=1),
            event_id=UUID(int=2),
        )
        for status, same, valid in histories
    ]
    assert RequirementAgreementService().classify(rows, legacy, None).state == state


def test_legacy_records_do_not_become_fixed_agreement(client, db, trace_setup):
    """旧approvedは対象未確認として残し、現在内容の合意へ昇格しない。"""
    from app.models.requirement import RequirementApproval

    c = trace_setup()
    db.add(
        RequirementApproval(
            document_id=c.doc.id,
            target_type="requirement_item",
            target_id=c.req.id,
            status="approved",
            approver_id=c.user.id,
            requested_by=c.user.id,
        )
    )
    db.commit()
    row = read(client, c.project.id, [c.req.id]).json()["items"][0]
    assert row["internal"]["state"] == "legacy_unverified"
    assert row["internal"]["approval_id"] is None
    assert row["external"]["state"] == "unagreed"


def test_exception_document_approval_does_not_agree_requirement(
    client, approval_context
):
    """例外付き正式承認と、含まれる個別要件の合意を分ける。"""
    c = approval_context
    row, _ = request(client, c, scope_data(c, document=True, exceptions=True))
    authorize_as(client, c["approver"])
    result, _ = decide(client, c, row)
    assert result["status"] == "approved_with_exceptions"
    assert (
        read(client, c["project"].id, [c["req"].id]).json()["items"][0]["internal"][
            "state"
        ]
        == "unagreed"
    )


def test_external_judgment_stays_external_and_history_survives_revocation(
    client, db, guest_approval_context
):
    """実外部bindingを使い、失効後の社内証跡と外部アクセスを分ける。"""
    c = guest_approval_context
    row, _ = guest_request(client, c)
    rid = c["req"].id
    result = read(client, c["project"].id, [rid]).json()["items"][0]
    assert result["internal"]["state"] == "unagreed"
    assert result["external"]["state"] == "pending"
    guest_login(client, db, c)
    path = f"/shared/publications/{c['publication_id']}/approval-requests/{row['id']}"
    result, _ = guest_decision(client, path)
    assert result.status_code == 200, result.text
    assert read(client, c["project"].id, [rid]).status_code == 403
    authorize_as(client, c["user"])
    result = read(client, c["project"].id, [rid]).json()["items"][0]
    assert result["external"]["state"] == "agreed"
    assert result["internal"]["state"] == "unagreed"
    revoked = client.post(
        f"/projects/{c['project'].id}/artifact-publications/{c['publication_id']}/grants/{c['grant']['id']}/revoke",
        json={"request_id": str(uuid4()), "version": 1, "reason": "確認期間終了"},
    )
    assert revoked.status_code == 200, revoked.text
    assert (
        read(client, c["project"].id, [rid]).json()["items"][0]["external"]["state"]
        == "agreed"
    )
    guest_login(client, db, c)
    assert client.get(path).status_code == 404


def test_batch_budget_and_constant_business_sql(client, db, trace_setup):
    """20→100件で業務SQLを増やさず、8MiB batchもDB側で抑える。"""
    import json
    from pathlib import Path

    from sqlalchemy import event

    from app.db.session import engine
    from app.models.requirement import Requirement

    c = trace_setup()
    rows = [
        Requirement(
            document_id=c.doc.id,
            requirement_code=f"PERF-{i}",
            requirement_type="functional",
            title="読取対象",
            description="通常本文",
        )
        for i in range(100)
    ]
    db.add_all(rows)
    db.commit()
    ids = [r.id for r in rows]
    sizes, counts = {}, {}
    for size in (20, 100):
        statements = []

        def count(conn, cursor, statement, parameters, context, executemany):
            if any(
                s in statement
                for s in (
                    "detail_budget AS",
                    "current_subjects(",
                    "requirement_approvals",
                )
            ):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", count)
        try:
            response = read(client, c.project.id, ids[:size])
        finally:
            event.remove(engine, "before_cursor_execute", count)
        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) == size
        sizes[size], counts[size] = len(response.content), len(statements)
    assert counts[20] == counts[100] and 0 < counts[100] <= 12
    assert sizes[100] <= 512 * 1024
    for row in rows[:20]:
        row.description = "X" * 500000
    db.commit()
    material = RequirementAgreementRepository().subjects(db, c.project.id, ids[:20])
    assert any(row["subject"] is None for row in material)
    assert (
        sum(
            len(json.dumps(row["subject"], ensure_ascii=False).encode())
            for row in material
            if row["subject"] is not None
        )
        <= 8 * 1024 * 1024
    )
    db.add_all(
        [
            RequirementDetail(
                requirement_id=c.req.id,
                detail_type="functional",
                detail_json={"index": i},
            )
            for i in range(501)
        ]
    )
    db.commit()
    assert (
        read(client, c.project.id, [c.req.id]).json()["items"][0]["internal"]["reason"]
        == "size_limit"
    )
    Path(".verify/agreements-performance.json").write_text(
        json.dumps(
            {"business_sql": counts, "response_bytes": sizes, "batch_budget": True}
        )
        + "\n"
    )
