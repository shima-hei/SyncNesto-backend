"""版固定承認・例外・従来pending切替を実DBとAPIで確認する。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.models.fixed_approval import (
    FixedApprovalEvent,
    FixedApprovalRequest,
    LegacyApprovalImport,
)
from app.models.requirement import Requirement
from app.models.user import User
from app.services.audit_log import AuditLogService
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_releases import context as context
from tests.routers.test_artifact_releases import save


@pytest.mark.parametrize("state", ["session_revoked", "mfa", "csrf"])
def test_decision_rechecks_session_mfa_and_csrf(
    client, db, approval_context, monkeypatch, state
):
    """判断前の確認を済ませても、失効した認証状態で判断できない。"""
    from app.core.config import settings
    from app.models.session import UserSession

    ctx = approval_context
    row, _ = request(client, ctx)
    sid = authorize_as(client, ctx["approver"])
    preview = client.post(f"{ctx['approvals']}/{row['id']}/decision-preview")
    assert preview.status_code == 200
    data = dict(
        version=1,
        operation_id=str(uuid4()),
        decision="approve",
        expected_review_digest=preview.json()["review_digest"],
        confirm_fixed_scope=True,
    )
    if state == "session_revoked":
        db.get(UserSession, sid).revoked_at = datetime.now(UTC)
        db.commit()
    elif state == "mfa":
        monkeypatch.setattr(settings, "mfa_required", True)
    else:
        client.headers.pop(settings.csrf_header_name)
    result = client.post(f"{ctx['approvals']}/{row['id']}/decisions", json=data)
    assert result.status_code in (401, 403), result.text
    db.expire_all()
    assert db.get(FixedApprovalRequest, row["id"]).status == "requested"
    assert db.scalar(select(func.count()).select_from(FixedApprovalEvent)) == 1


def test_source_permission_loss_hides_count_and_decision(client, db, approval_context):
    """同梱設計のreadを失えば、指定本人でも存在・件数・判断を返さない。"""
    from sqlalchemy import delete

    from app.models.rbac import Permission, Role, RolePermission

    ctx = approval_context
    row, _ = request(client, ctx)
    permission = db.scalar(
        select(Permission.id).where(Permission.code == "document:read")
    )
    role = db.scalar(
        select(Role.id).where(Role.key == "project_admin", Role.scope == "project")
    )
    db.execute(
        delete(RolePermission).where(
            RolePermission.role_id == role, RolePermission.permission_id == permission
        )
    )
    db.commit()
    authorize_as(client, ctx["approver"])
    assert client.get(ctx["approvals"]).json()["total"] == 0
    assert client.get(f"{ctx['approvals']}/{row['id']}").status_code == 403
    assert (
        client.post(f"{ctx['approvals']}/{row['id']}/decision-preview").status_code
        == 403
    )


def test_basis_cursor_rejects_tamper_and_changed_agreement(
    client, db, approval_context
):
    """根拠のページ間で合意が変わったら、混ざった一覧を返さない。"""
    ctx = approval_context
    db.add(
        Requirement(
            document_id=ctx["doc"].id,
            section_id=ctx["section"].id,
            requirement_code="REQ-002",
            requirement_type="functional",
            title="二つ目の要件",
            owner_id=ctx["user"].id,
        )
    )
    db.commit()
    ctx["release"], _ = save(client, ctx)
    ctx["nodes"] = client.get(f"{ctx['base']}/{ctx['release']['id']}/nodes").json()[
        "items"
    ]
    target = next(
        node for node in ctx["nodes"] if node["node_kind"] == "requirement_document"
    )
    params = dict(
        release_id=ctx["release"]["id"], target_node_id=target["id"], page_size=1
    )
    first = client.get(ctx["approvals"] + "/basis", params=params).json()
    assert first["total"] == 2 and first["next_cursor"]
    assert (
        client.get(
            ctx["approvals"] + "/basis",
            params={**params, "cursor": "x" + first["next_cursor"][1:]},
        ).status_code
        == 409
    )
    row, _ = request(client, ctx)
    assert (
        client.get(
            ctx["approvals"] + "/basis",
            params={**params, "cursor": first["next_cursor"]},
        ).status_code
        == 409
    )
    assert row["status"] == "requested"


def test_review_input_size_and_exception_limits(client, approval_context):
    """容量や例外上限を超える入力を、無言で省略せず拒否する。"""
    ctx = approval_context
    data = scope_data(ctx, document=True)
    oversized = [dict(node_id=str(uuid4()), reason="あ" * 2000) for _ in range(180)]
    result = client.post(
        ctx["approvals"] + "/preview", json={**data, "exceptions": oversized}
    )
    assert result.status_code == 413, result.text
    result = client.post(
        ctx["approvals"] + "/preview",
        json={
            **data,
            "exceptions": [
                dict(node_id=str(uuid4()), reason="理由") for _ in range(1001)
            ],
        },
    )
    assert result.status_code == 422


@pytest.fixture
def approval_context(client, db, context, create_test_user, assign_project_role):
    """申請者・指定本人と、一つの固定文書を作る。"""
    approver = create_test_user(email=f"approver-{uuid4()}@example.com")
    manager = create_test_user(email=f"manager-{uuid4()}@example.com")
    assign_project_role(
        user=approver, project=context["project"], role_key="project_admin"
    )
    assign_project_role(user=manager, project=context["project"], role_key="manager")
    release, _ = save(client, context)
    nodes = client.get(f"{context['base']}/{release['id']}/nodes").json()["items"]
    return {
        **context,
        "approver": approver,
        "manager": manager,
        "release": release,
        "nodes": nodes,
        "approvals": f"/projects/{context['project'].id}/fixed-approvals",
        "migrations": f"/projects/{context['project'].id}/approval-migrations",
    }


def scope_data(ctx, *, document=False, exceptions=False):
    """個別／文書を明示して、必要な例外理由を入力する。"""
    kind = "requirement_document" if document else "requirement"
    node = next(item for item in ctx["nodes"] if item["node_kind"] == kind)
    return dict(
        release_id=ctx["release"]["id"],
        target_node_id=node["id"],
        attachments_none=True,
        approver_id=ctx["approver"].id,
        exceptions=[
            dict(node_id=item["id"], reason="未確定の条件を例外として確認")
            for item in ctx["nodes"]
            if item["node_kind"] == "requirement"
        ]
        if exceptions
        else [],
    )


def request(client, ctx, data=None):
    """対象の確認hashを渡して申請する。"""
    data = data or scope_data(ctx)
    preview = client.post(ctx["approvals"] + "/preview", json=data)
    assert preview.status_code == 200, preview.text
    body = {
        **data,
        "request_id": str(uuid4()),
        "expected_preview_digest": preview.json()["preview_digest"],
    }
    response = client.post(ctx["approvals"], json=body)
    assert response.status_code == 201, response.text
    return response.json(), body


def decide(client, ctx, row, *, decision="approve", comment=None):
    """本人として対象の変化と例外を確認してから判断する。"""
    base = f"{ctx['approvals']}/{row['id']}"
    preview = client.post(base + "/decision-preview")
    assert preview.status_code == 200, preview.text
    body = dict(
        version=row["version"],
        operation_id=str(uuid4()),
        decision=decision,
        comment=comment,
        expected_review_digest=preview.json()["review_digest"],
        confirm_fixed_scope=True,
        confirm_changed_or_unknown=True,
        confirm_exceptions=True,
    )
    response = client.post(base + "/decisions", json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def test_designated_actor_and_idempotency(client, db, approval_context):
    """自己・代理を拒否し、同一判断の再送はイベントを増やさない。"""
    ctx = approval_context
    row, body = request(client, ctx)
    assert client.post(ctx["approvals"], json=body).status_code == 200
    assert (
        client.post(f"{ctx['approvals']}/{row['id']}/decision-preview").status_code
        == 403
    )
    authorize_as(client, ctx["manager"])
    assert (
        client.post(f"{ctx['approvals']}/{row['id']}/decision-preview").status_code
        == 403
    )
    authorize_as(client, ctx["approver"])
    decided, decision_body = decide(client, ctx, row)
    assert decided["status"] == "approved"
    assert decided["events"][-1]["actor_id"] == ctx["approver"].id
    again = client.post(f"{ctx['approvals']}/{row['id']}/decisions", json=decision_body)
    assert again.status_code == 200, again.text
    assert db.scalar(select(func.count()).select_from(FixedApprovalEvent)) == 2
    different = client.post(
        f"{ctx['approvals']}/{row['id']}/decisions",
        json={**decision_body, "operation_id": str(uuid4())},
    )
    assert different.status_code == 409


def test_exception_document_keeps_individual_unagreed(client, db, approval_context):
    """例外あり正式承認は要件の状態やテストを合意済みにしない。"""
    ctx = approval_context
    missing = client.post(
        ctx["approvals"] + "/preview", json=scope_data(ctx, document=True)
    )
    assert missing.status_code == 409
    row, _ = request(client, ctx, scope_data(ctx, document=True, exceptions=True))
    assert row["scope_manifest"]["exception_count"] == 1
    authorize_as(client, ctx["approver"])
    decided, _ = decide(client, ctx, row)
    assert decided["status"] == "approved_with_exceptions"
    db.expire_all()
    assert db.get(Requirement, ctx["req"].id).status == "draft"
    bases = client.get(ctx["approvals"] + "/basis", params={"approval_id": row["id"]})
    assert bases.status_code == 200, bases.text
    assert bases.json()["items"][0]["reason"]
    assert bases.json()["items"][0]["state"] == "unagreed"


def test_individual_agreement_is_explicit_document_basis(client, approval_context):
    """全件合意でも文書を自動承認せず、根拠付きで別依頼にする。"""
    ctx = approval_context
    individual, _ = request(client, ctx)
    authorize_as(client, ctx["approver"])
    decide(client, ctx, individual)
    authorize_as(client, ctx["user"])
    formal, _ = request(client, ctx, scope_data(ctx, document=True))
    assert formal["status"] == "requested"
    assert formal["scope_manifest"]["exception_count"] == 0
    basis = client.get(
        ctx["approvals"] + "/basis", params={"approval_id": formal["id"]}
    ).json()["items"][0]
    assert basis["agreed_approval_id"] == individual["id"]
    authorize_as(client, ctx["approver"])
    assert decide(client, ctx, formal)[0]["status"] == "approved"


def test_changed_content_needs_new_review_and_keeps_old_request(
    client, db, approval_context
):
    """元versionだけでなく子本文の変化も確認し、旧依頼は保持する。"""
    ctx = approval_context
    row, _ = request(client, ctx)
    authorize_as(client, ctx["approver"])
    base = f"{ctx['approvals']}/{row['id']}"
    preview = client.post(base + "/decision-preview").json()
    assert preview["observation"]["body_comparison"] == "equal"
    ctx["req"].description = "申請後に条件を変更"
    db.commit()
    old = dict(
        version=1,
        operation_id=str(uuid4()),
        decision="approve",
        expected_review_digest=preview["review_digest"],
        confirm_fixed_scope=True,
    )
    assert client.post(base + "/decisions", json=old).status_code == 409
    new_preview = client.post(base + "/decision-preview").json()
    assert new_preview["observation"]["body_comparison"] == "changed"
    assert client.get(base).json()["status"] == "requested"
    assert decide(client, ctx, row)[0]["status"] == "approved"


def test_legacy_switch_blocks_old_api_and_preserves_history(
    client, db, approval_context
):
    """旧行の状態は変えず、移行後は新方式だけが判断できる。"""
    ctx = approval_context
    response = client.post(
        f"/projects/{ctx['project'].id}/approvals/request",
        json={
            "target_type": "requirement_item",
            "target_id": ctx["req"].id,
            "approver_id": ctx["approver"].id,
            "comment": "従来の確認依頼",
        },
    )
    assert response.status_code == 201, response.text
    legacy = response.json()
    data = dict(
        legacy_approval_id=legacy["id"], mode="switch", new_request=scope_data(ctx)
    )
    preview = client.post(ctx["migrations"] + "/preview", json=data)
    assert preview.status_code == 200, preview.text
    data.update(
        request_id=str(uuid4()),
        expected_preview_digest=preview.json()["preview_digest"],
    )
    migrated = client.post(ctx["migrations"], json=data)
    assert migrated.status_code == 201, migrated.text
    result = migrated.json()
    assert result["migrated_to"]
    assert result["source"]["status"] == "requested"
    assert not result["evidence"]["subject_verified"]
    assert client.post(ctx["migrations"], json=data).status_code == 200
    authorize_as(client, ctx["approver"])
    old = client.post(
        f"/projects/{ctx['project'].id}/approvals/{legacy['id']}/approve",
        json={"comment": "旧経路で判断"},
    )
    assert old.status_code == 409, old.text
    assert old.json()["code"] == "APPROVAL_MIGRATED"
    row = client.get(f"{ctx['approvals']}/{result['migrated_to']}").json()
    assert decide(client, ctx, row)[0]["status"] == "approved"
    listing = client.get(ctx["approvals"])
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 2
    assert db.scalar(select(func.count()).select_from(LegacyApprovalImport)) == 1
    assert db.scalar(select(func.count()).select_from(FixedApprovalRequest)) == 1


def test_candidates_do_not_expand_existing_permissions(client, approval_context):
    """managerを承認者に自動昇格せず、指定本人候補をサーバーで絞る。"""
    ctx = approval_context
    candidates = client.get(
        ctx["approvals"] + "/approver-candidates",
        params={"release_id": ctx["release"]["id"]},
    )
    assert candidates.status_code == 200, candidates.text
    ids = {item["id"] for item in candidates.json()["items"]}
    assert ctx["approver"].id in ids
    assert ctx["manager"].id not in ids
    assert ctx["user"].id not in ids
    wrong = client.post(
        ctx["approvals"] + "/preview",
        json={**scope_data(ctx), "approver_id": ctx["user"].id},
    )
    assert wrong.status_code == 400


@pytest.mark.parametrize("kind", ["rejected", "cancelled"])
def test_reasons_and_terminal_event_are_immutable(client, db, approval_context, kind):
    """空の理由・終判断の書換えを拒否する。"""
    ctx = approval_context
    row, _ = request(client, ctx)
    base = f"{ctx['approvals']}/{row['id']}"
    if kind == "cancelled":
        data = dict(version=1, operation_id=str(uuid4()), reason=" ")
        assert client.post(base + "/cancel", json=data).status_code == 400
        data["reason"] = "対象を確認し直すため"
        result = client.post(base + "/cancel", json=data)
        assert result.status_code == 200, result.text
        assert client.post(base + "/cancel", json=data).status_code == 200
    else:
        authorize_as(client, ctx["approver"])
        preview = client.post(base + "/decision-preview").json()
        empty = dict(
            version=1,
            operation_id=str(uuid4()),
            decision="reject",
            comment=" ",
            expected_review_digest=preview["review_digest"],
            confirm_fixed_scope=True,
        )
        assert client.post(base + "/decisions", json=empty).status_code == 400
        decide(client, ctx, row, decision="reject", comment="受け入れ条件が不足")
    db.expire_all()
    event = db.scalar(select(FixedApprovalEvent).where(FixedApprovalEvent.seq == 2))
    event.reason = "履歴を上書き"
    with pytest.raises(DBAPIError):
        db.commit()
    db.rollback()
    assert client.get(base).json()["status"] == kind


def test_two_judgments_have_one_winner(client, db, approval_context):
    """別操作IDで同時判断しても終イベントは一つだけ。"""
    ctx = approval_context
    row, _ = request(client, ctx)
    authorize_as(client, ctx["approver"])
    base = f"{ctx['approvals']}/{row['id']}"
    preview = client.post(base + "/decision-preview").json()
    data = dict(
        version=1,
        decision="approve",
        expected_review_digest=preview["review_digest"],
        confirm_fixed_scope=True,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.post(
                    base + "/decisions", json={**data, "operation_id": str(uuid4())}
                ),
                range(2),
            )
        )
    assert sorted(item.status_code for item in responses) == [200, 409]
    assert (
        db.scalar(
            select(func.count())
            .select_from(FixedApprovalEvent)
            .where(FixedApprovalEvent.seq == 2)
        )
        == 1
    )


def test_audit_failure_is_atomic(client, db, approval_context, monkeypatch):
    """監査失敗を無視して承認だけを確定させない。"""
    ctx = approval_context
    row, _ = request(client, ctx)
    authorize_as(client, ctx["approver"])
    base = f"{ctx['approvals']}/{row['id']}"
    preview = client.post(base + "/decision-preview").json()

    def fail(*args, **kwargs):
        raise RuntimeError("dummy audit failure")

    monkeypatch.setattr(AuditLogService, "record", fail)
    with pytest.raises(RuntimeError, match="dummy audit failure"):
        client.post(
            base + "/decisions",
            json=dict(
                version=1,
                operation_id=str(uuid4()),
                decision="approve",
                expected_review_digest=preview["review_digest"],
                confirm_fixed_scope=True,
            ),
        )
    db.expire_all()
    assert db.scalar(select(FixedApprovalRequest.status)) == "requested"
    assert db.scalar(select(func.count()).select_from(FixedApprovalEvent)) == 1


def test_archive_then_switch_preserves_both_snapshots(client, db, approval_context):
    """先に取込を済ませた同じ旧記録でも、後でpending切替できる。"""
    ctx = approval_context
    old = client.post(
        f"/projects/{ctx['project'].id}/approvals/request",
        json=dict(
            target_type="requirement_item",
            target_id=ctx["req"].id,
            approver_id=ctx["approver"].id,
        ),
    ).json()
    for mode in ("archive", "switch"):
        data: dict = dict(legacy_approval_id=old["id"], mode=mode)
        if mode == "switch":
            data["new_request"] = scope_data(ctx)
        preview = client.post(ctx["migrations"] + "/preview", json=data)
        assert preview.status_code == 200, preview.text
        response = client.post(
            ctx["migrations"],
            json={
                **data,
                "request_id": str(uuid4()),
                "expected_preview_digest": preview.json()["preview_digest"],
            },
        )
        assert response.status_code == 201, response.text
    assert db.scalar(select(func.count()).select_from(LegacyApprovalImport)) == 2
    assert client.get(ctx["approvals"]).json()["total"] == 2


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_unreadable_fixed_entries_are_absent_from_count(
    client, approval_context, create_test_user, assign_project_role, role
):
    """固定版権限がない場合、新依頼の存在・件数・根拠を返さない。"""
    ctx = approval_context
    row, _ = request(client, ctx)
    reader = create_test_user(email=f"{role}@example.com")
    assign_project_role(user=reader, project=ctx["project"], role_key=role)
    authorize_as(client, reader)
    listing = client.get(ctx["approvals"])
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 0
    assert client.get(f"{ctx['approvals']}/{row['id']}").status_code == 403
    assert (
        client.get(
            ctx["approvals"] + "/basis", params={"approval_id": row["id"]}
        ).status_code
        == 403
    )


def test_other_project_cannot_supply_a_release(
    client, approval_context, create_test_project, assign_project_role
):
    """両Projectに所属しても、別Projectの固定本文を申請できない。"""
    ctx = approval_context
    other = create_test_project()
    assign_project_role(user=ctx["user"], project=other, role_key="project_admin")
    response = client.post(
        f"/projects/{other.id}/fixed-approvals/preview", json=scope_data(ctx)
    )
    assert response.status_code == 404, response.text


def test_identity_deactivation_between_preview_and_decision(
    client, db, approval_context
):
    """preview時の権限を、失効後の判断へ再利用しない。"""
    ctx = approval_context
    row, _ = request(client, ctx)
    authorize_as(client, ctx["approver"])
    base = f"{ctx['approvals']}/{row['id']}"
    preview = client.post(base + "/decision-preview").json()
    actor = db.get(User, ctx["approver"].id)
    actor.is_active = False
    db.commit()
    response = client.post(
        base + "/decisions",
        json=dict(
            version=1,
            operation_id=str(uuid4()),
            decision="approve",
            expected_review_digest=preview["review_digest"],
            confirm_fixed_scope=True,
        ),
    )
    assert response.status_code in {401, 403}, response.text
    db.expire_all()
    assert db.scalar(select(FixedApprovalRequest.status)) == "requested"


def test_terminal_legacy_reconfirmation_links_history_without_backdating(
    client, db, approval_context
):
    """旧承認を引き継いだだけで現在版を承認済みにせず、今回の本人判断を残す。"""
    ctx = approval_context
    old = client.post(
        f"/projects/{ctx['project'].id}/approvals/request",
        json=dict(
            target_type="requirement_item",
            target_id=ctx["req"].id,
            approver_id=ctx["approver"].id,
        ),
    ).json()
    authorize_as(client, ctx["approver"])
    approved = client.post(
        f"/projects/{ctx['project'].id}/approvals/{old['id']}/approve",
        json={"comment": "以前の判断"},
    ).json()
    authorize_as(client, ctx["user"])
    data = dict(legacy_approval_id=old["id"], mode="archive")
    preview = client.post(ctx["migrations"] + "/preview", json=data).json()
    imported = client.post(
        ctx["migrations"],
        json={
            **data,
            "request_id": str(uuid4()),
            "expected_preview_digest": preview["preview_digest"],
        },
    )
    assert imported.status_code == 201, imported.text
    row, _ = request(
        client, ctx, {**scope_data(ctx), "legacy_import_id": imported.json()["id"]}
    )
    assert row["status"] == "requested"
    assert row["legacy_import_id"] == imported.json()["id"]
    assert row["events"][0]["occurred_at"] != approved["approved_at"]
    assert not imported.json()["evidence"]["subject_verified"]
    authorize_as(client, ctx["approver"])
    assert decide(client, ctx, row)[0]["status"] == "approved"


def test_imported_pending_cannot_bypass_switch_guard(client, approval_context):
    """archiveした旧pendingを通常新規作成で二重判断可能にしない。"""
    ctx = approval_context
    old = client.post(
        f"/projects/{ctx['project'].id}/approvals/request",
        json=dict(
            target_type="requirement_item",
            target_id=ctx["req"].id,
            approver_id=ctx["approver"].id,
        ),
    ).json()
    data = dict(legacy_approval_id=old["id"], mode="archive")
    preview = client.post(ctx["migrations"] + "/preview", json=data).json()
    imported = client.post(
        ctx["migrations"],
        json={
            **data,
            "request_id": str(uuid4()),
            "expected_preview_digest": preview["preview_digest"],
        },
    ).json()
    bad = client.post(
        ctx["approvals"] + "/preview",
        json={**scope_data(ctx), "legacy_import_id": imported["id"]},
    )
    assert bad.status_code == 409, bad.text


def test_archived_history_survives_original_approval_deletion(
    client, db, approval_context
):
    """旧行が消えても取込時点の履歴と一覧を取得できる。"""
    from app.models.requirement import RequirementApproval

    ctx = approval_context
    old = client.post(
        f"/projects/{ctx['project'].id}/approvals/request",
        json=dict(
            target_type="requirement_item",
            target_id=ctx["req"].id,
            approver_id=ctx["approver"].id,
        ),
    ).json()
    data = dict(legacy_approval_id=old["id"], mode="archive")
    preview = client.post(ctx["migrations"] + "/preview", json=data).json()
    imported = client.post(
        ctx["migrations"],
        json={
            **data,
            "request_id": str(uuid4()),
            "expected_preview_digest": preview["preview_digest"],
        },
    ).json()
    db.delete(db.get(RequirementApproval, old["id"]))
    db.commit()
    read = client.get(ctx["migrations"] + "/" + imported["id"])
    assert read.status_code == 200, read.text
    assert read.json()["source"]["id"] == old["id"]
    listing = client.get(ctx["approvals"])
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["import_id"] == imported["id"]
