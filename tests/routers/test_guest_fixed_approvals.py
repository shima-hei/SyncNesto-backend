"""外部本人の固定判断・正本整合・失効を実DBで検証する。"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.artifact_publication import ArtifactPublicationNode
from app.models.fixed_approval import FixedApprovalEvent, FixedApprovalRequest
from app.models.guest_fixed_approval import FixedApprovalGuestBinding
from app.models.session import UserSession
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_releases import context as context  # noqa: F401
from tests.routers.test_publication_access import access_fixture as access_fixture  # noqa: F401
from tests.routers.test_publication_access import publish


@pytest.fixture
def guest_approval_context(client, db, access_fixture, monkeypatch):
    """社内担当と有効な共有先・公開本文を用意する。"""
    monkeypatch.setattr(settings, "guest_fixed_approvals_enabled", True)
    ctx = access_fixture
    publish(client, ctx, both=True)
    identity = ctx["publication"]["content"]["id"]
    grants = client.get(
        f"/projects/{ctx['project'].id}/artifact-publications/{identity}/grants"
    ).json()["items"]
    nodes = list(
        db.scalars(
            select(ArtifactPublicationNode).where(
                ArtifactPublicationNode.publication_id == UUID(identity)
            )
        )
    )
    return {
        **ctx,
        "publication_id": identity,
        "public_nodes": nodes,
        "grant": next(
            row
            for row in grants
            if row["participation_id"] == str(ctx["guests"][0][1].id)
        ),
        "requests": f"/projects/{ctx['project'].id}/guest-fixed-approvals",
    }


def guest_login(client, db, ctx, *, index=0, verified=True):
    """通常セッションの実MFA結果を明示する。"""
    user = ctx["guests"][index][0]
    sid = authorize_as(client, user)
    if verified:
        session = db.get(UserSession, sid)
        session.mfa_method = "totp"
        session.mfa_verified_at = datetime.now(UTC)
        session.mfa_security_version = user.mfa_security_version
        db.commit()
    return sid


def create_request(client, ctx, kind="requirement"):
    """相手向けの全例外と確認digestを渡して保存する。"""
    node = next(n for n in ctx["public_nodes"] if n.node_kind == kind)
    data: dict[str, Any] = dict(
        publication_id=ctx["publication_id"],
        target_node_id=str(node.id),
        grant_id=ctx["grant"]["id"],
        request_note="この固定内容を確認してください",
    )
    basis = client.post(
        ctx["requests"] + "/basis-preview",
        json={k: data[k] for k in ("publication_id", "target_node_id")},
    )
    assert basis.status_code == 200, basis.text
    data["exceptions"] = [
        dict(node_id=row["node_id"], reason="外部合意不足を今回明示確認")
        for row in basis.json()["items"]
        if row["state"] != "agreed"
    ]
    preview = client.post(ctx["requests"] + "/preview", json=data)
    assert preview.status_code == 200, preview.text
    body = {
        **data,
        "request_id": str(uuid4()),
        "expected_preview_digest": preview.json()["preview_digest"],
        "confirm_public_scope": True,
    }
    result = client.post(ctx["requests"], json=body)
    assert result.status_code == 201, result.text
    return result.json(), body


def approval_path(ctx, row):
    """新しい外部専用経路。"""
    return f"/shared/publications/{ctx['publication_id']}/approval-requests/{row['id']}"


def decision(client, path, *, reject=False):
    """実際の確認proofと固定対象への明示確認を渡す。"""
    preview = client.post(path + "/decision-preview")
    assert preview.status_code == 200, preview.text
    data = dict(
        version=preview.json()["version"],
        review_proof=preview.json()["review_proof"],
        operation_id=str(uuid4()),
        decision="reject" if reject else "approve",
        comment="条件を再確認してください" if reject else None,
        confirm_fixed_scope=True,
        confirm_changed_or_unknown=True,
        confirm_exceptions=True,
    )
    return client.post(path + "/decisions", json=data), data


@pytest.mark.parametrize(
    "kind", ["requirement", "requirement_document", "specification"]
)
def test_guest_judgment_uses_existing_canonical_request(
    client, db, guest_approval_context, kind
):
    """既存の判断正本へ一度保存し、可変本文・内部roleを変更しない。"""
    ctx = guest_approval_context
    row, create_body = create_request(client, ctx, kind)
    assert row["audience"] == "guest" and row["can_decide"] is False
    replay = client.post(ctx["requests"], json=create_body)
    assert replay.status_code == 200 and replay.json()["id"] == row["id"]
    guest_login(client, db, ctx)
    path = approval_path(ctx, row)
    metadata = client.get(path)
    assert metadata.status_code == 200, metadata.text
    assert (
        "scope_manifest" not in metadata.json() and "approver_id" not in metadata.json()
    )
    listing = client.get("/shared/publications/approval-requests")
    assert listing.status_code == 200 and listing.json()["total"] == 1, listing.text
    result, body = decision(client, path)
    assert result.status_code == 200, result.text
    expected = (
        "approved_with_exceptions" if kind == "requirement_document" else "approved"
    )
    assert result.json()["status"] == expected
    assert client.post(path + "/decisions", json=body).json()["status"] == expected
    db.expire_all()
    assert db.get(FixedApprovalRequest, UUID(row["id"])).status == expected
    assert db.scalar(select(func.count()).select_from(FixedApprovalGuestBinding)) == 1
    assert db.scalar(select(func.count()).select_from(FixedApprovalEvent)) == 2


def test_other_recipient_and_global_mfa_false_do_not_grant_judgment(
    client, db, guest_approval_context
):
    """同じ資料を読める別人やMFA未検証の本人は判断できない。"""
    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    path = approval_path(ctx, row)
    guest_login(client, db, ctx, index=1)
    assert client.get(path).status_code == 404
    assert client.get("/shared/publications/approval-requests").json()["total"] == 0
    guest_login(client, db, ctx, verified=False)
    assert settings.mfa_required is False
    result = client.post(path + "/decision-preview")
    assert result.status_code == 403 and result.json()["code"] == "MFA_REQUIRED"
    assert (
        client.get(f"/shared/publications/{ctx['publication_id']}").status_code == 200
    )


def test_internal_cannot_decide_guest_request_but_can_cancel(
    client, db, guest_approval_context
):
    """内部判断経路は開放せず、失効後に取消して新規依頼できる。"""
    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    internal = f"/projects/{ctx['project'].id}/fixed-approvals/{row['id']}"
    assert client.post(internal + "/decision-preview").status_code == 403
    result = client.post(
        internal + "/cancel",
        json=dict(version=1, operation_id=str(uuid4()), reason="今回の依頼を取消"),
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "cancelled"
    row2, _ = create_request(client, ctx)
    assert row2["id"] != row["id"]


def test_revocation_blocks_history_replay_and_old_binding(
    client, db, guest_approval_context
):
    """再許可しても旧依頼を復活させず、取消と新依頼を要求する。"""
    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    guest_login(client, db, ctx)
    path = approval_path(ctx, row)
    result, body = decision(client, path)
    assert result.status_code == 200
    authorize_as(client, ctx["user"])
    base = (
        f"/projects/{ctx['project'].id}/artifact-publications/{ctx['publication_id']}"
    )
    revoked = client.post(
        base + f"/grants/{ctx['grant']['id']}/revoke",
        json=dict(request_id=str(uuid4()), version=1, reason="確認期間終了"),
    )
    assert revoked.status_code == 200, revoked.text
    guest_login(client, db, ctx)
    for suffix in ("", "/bases", "/decision-preview"):
        response = (
            client.post(path + suffix)
            if suffix == "/decision-preview"
            else client.get(path + suffix)
        )
        assert response.status_code == 404, response.text
    assert client.post(path + "/decisions", json=body).status_code == 404
    authorize_as(client, ctx["user"])
    from datetime import timedelta

    current = client.get(base).json()
    added = client.post(
        base + "/grants",
        json=dict(
            request_id=str(uuid4()),
            publication_version=current["publication_version"],
            expected_state="published",
            public_digest=current["content"]["public_digest"],
            confirmed_full_content=True,
            recipients=[
                dict(
                    participation_id=str(ctx["guests"][0][1].id),
                    version=1,
                    expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                )
            ],
        ),
    )
    assert added.status_code == 200, added.text
    guest_login(client, db, ctx)
    assert client.get(path).status_code == 404
    assert client.get("/shared/publications/approval-requests").json()["total"] == 0
    db.expire_all()
    assert db.get(FixedApprovalRequest, UUID(row["id"])).status == "approved"


def test_pending_revocation_can_be_cancelled_without_live_publication(
    client, db, guest_approval_context
):
    """公開停止で判断不能になったpendingも、内部担当が取消可能。"""
    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    base = (
        f"/projects/{ctx['project'].id}/artifact-publications/{ctx['publication_id']}"
    )
    response = client.post(
        base + "/withdraw",
        json=dict(request_id=str(uuid4()), version=2, reason="公開停止"),
    )
    assert response.status_code == 200, response.text
    internal = f"/projects/{ctx['project'].id}/fixed-approvals/{row['id']}"
    detail = client.get(internal)
    assert (
        detail.status_code == 200
        and detail.json()["guest_context"]["can_access"] is False
    )
    cancelled = client.post(
        internal + "/cancel",
        json=dict(version=1, operation_id=str(uuid4()), reason="再依頼へ切替"),
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled", (
        cancelled.text
    )


def test_external_agreement_is_the_only_normal_document_basis(
    client, db, guest_approval_context, create_test_user, assign_project_role
):
    """社内approvedは代用せず、外部の同一内容だけを文書根拠にする。"""
    from app.models.artifact_publication import ArtifactPublication
    from tests.routers.test_fixed_approvals import decide, request

    ctx = guest_approval_context
    pub = db.get(ArtifactPublication, UUID(ctx["publication_id"]))
    release = client.get(f"{ctx['base']}/{pub.release_id}").json()
    nodes = client.get(f"{ctx['base']}/{pub.release_id}/nodes").json()["items"]
    approver = create_test_user(email=f"internal-{uuid4()}@example.com")
    assign_project_role(user=approver, project=ctx["project"], role_key="project_admin")
    inner = {
        **ctx,
        "release": release,
        "nodes": nodes,
        "approver": approver,
        "approvals": f"/projects/{ctx['project'].id}/fixed-approvals",
    }
    internal_row, _ = request(client, inner)
    authorize_as(client, approver)
    assert decide(client, inner, internal_row)[0]["status"] == "approved"
    authorize_as(client, ctx["user"])
    doc = next(n for n in ctx["public_nodes"] if n.node_kind == "requirement_document")
    data = dict(publication_id=ctx["publication_id"], target_node_id=str(doc.id))
    before = client.post(ctx["requests"] + "/basis-preview", json=data).json()
    assert before["items"][0]["state"] == "unagreed"
    ext, _ = create_request(client, ctx)
    guest_login(client, db, ctx)
    assert decision(client, approval_path(ctx, ext))[0].status_code == 200
    authorize_as(client, ctx["user"])
    after = client.post(ctx["requests"] + "/basis-preview", json=data).json()
    assert after["items"][0]["state"] == "agreed"
    assert "agreed_approval_id" not in after["items"][0]
    document, _ = create_request(client, ctx, "requirement_document")
    guest_login(client, db, ctx)
    result, _ = decision(client, approval_path(ctx, document))
    assert result.status_code == 200 and result.json()["status"] == "approved", (
        result.text
    )


def test_test_design_has_its_own_whole_graph_approval(
    client, db, guest_approval_context
):
    """因子水準・マトリクスを固定した設計書を独立承認する。"""
    from tests.routers.test_artifact_publications import create
    from tests.routers.test_artifact_test_design import freeze
    from tests.routers.test_test_designs import graph

    ctx = guest_approval_context
    base = f"/projects/{ctx['project'].id}/test-designs"
    blank = client.post(base, json={"name": "外部確認するテスト設計"})
    assert blank.status_code == 201, blank.text
    data = graph(blank.json())
    data["layout"]["cells"] = {"items:x:content": {"bold": True}}
    design = client.put(base + f"/{blank.json()['id']}", json=data)
    assert design.status_code == 200, design.text
    release, _ = freeze(client, ctx["base"], design.json())
    publication, _ = create(client, ctx, release)
    ctx = {
        **ctx,
        "publication": publication,
        "publication_id": publication["content"]["id"],
    }
    publish(client, ctx)
    ctx["public_nodes"] = list(
        db.scalars(
            select(ArtifactPublicationNode).where(
                ArtifactPublicationNode.publication_id == UUID(ctx["publication_id"])
            )
        )
    )
    ctx["grant"] = client.get(
        f"/projects/{ctx['project'].id}/artifact-publications/{ctx['publication_id']}/grants"
    ).json()["items"][0]
    row, _ = create_request(client, ctx, "test_design")
    guest_login(client, db, ctx)
    result, _ = decision(client, approval_path(ctx, row))
    assert (
        result.status_code == 200 and result.json()["target_kind"] == "test_design"
    ), result.text
    assert result.json()["status"] == "approved"


def test_two_external_decisions_have_one_terminal_event(
    client, db, guest_approval_context
):
    """実接続で二つの判断を競合させ、結果を一つに確定する。"""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    guest_login(client, db, ctx)
    path = approval_path(ctx, row)
    preview = client.post(path + "/decision-preview").json()
    barrier = Barrier(2)

    def send():
        barrier.wait(timeout=10)
        return client.post(
            path + "/decisions",
            json=dict(
                version=1,
                operation_id=str(uuid4()),
                review_proof=preview["review_proof"],
                decision="approve",
                confirm_fixed_scope=True,
                confirm_changed_or_unknown=True,
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: send(), range(2)))
    assert sorted(reply.status_code for reply in replies) == [200, 409]
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(FixedApprovalEvent)) == 2


def test_audit_failure_rolls_back_external_judgment(
    client, db, guest_approval_context, monkeypatch
):
    """監査保存に失敗した判断を部分確定しない。"""
    from app.services.audit_log import AuditLogService

    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    guest_login(client, db, ctx)

    def failed(*args, **kwargs):
        raise RuntimeError("isolated audit failure")

    monkeypatch.setattr(AuditLogService, "record", failed)
    with pytest.raises(RuntimeError, match="isolated audit failure"):
        decision(client, approval_path(ctx, row))
    db.expire_all()
    assert db.get(FixedApprovalRequest, UUID(row["id"])).status == "requested"
    assert db.scalar(select(func.count()).select_from(FixedApprovalEvent)) == 1


def test_guest_binding_and_terminal_scope_cannot_be_mutated(
    client, db, guest_approval_context
):
    """SQL直書きでも対応の変更と境界なしの終判断を拒否する。"""
    from sqlalchemy import update
    from sqlalchemy.exc import DBAPIError

    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    with pytest.raises(DBAPIError):
        db.execute(
            update(FixedApprovalGuestBinding)
            .where(FixedApprovalGuestBinding.approval_id == UUID(row["id"]))
            .values(public_scope_digest="0" * 64)
        )
        db.commit()
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(
            update(FixedApprovalRequest)
            .where(FixedApprovalRequest.id == UUID(row["id"]))
            .values(status="approved", version=2, finalized_at=datetime.now(UTC))
        )
        db.commit()
    db.rollback()
    assert db.get(FixedApprovalRequest, UUID(row["id"])).status == "requested"


def test_signed_expired_proof_and_changed_input_are_rejected(
    client, db, guest_approval_context
):
    """署名だけが正しくても期限切れなら確定せず、同一IDの差替えも拒否。"""
    import base64
    import json

    from app.services.fixed_approval_cursor import encode_cursor

    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    guest_login(client, db, ctx)
    path = approval_path(ctx, row)
    proof = client.post(path + "/decision-preview").json()["review_proof"]
    raw = base64.urlsafe_b64decode(proof + "=" * (-len(proof) % 4))
    scope = json.loads(raw[:-32])[3]
    expired = encode_cursor(
        0, "guest-approval-review", scope, int(datetime.now(UTC).timestamp()) - 1
    )
    response = client.post(
        path + "/decisions",
        json=dict(
            version=1,
            operation_id=str(uuid4()),
            review_proof=expired,
            decision="approve",
            confirm_fixed_scope=True,
        ),
    )
    assert response.status_code == 409
    result, body = decision(client, path)
    assert result.status_code == 200
    changed = client.post(path + "/decisions", json={**body, "comment": "差し替え"})
    assert changed.status_code == 409


@pytest.mark.parametrize("first", ["decision", "revoke"])
def test_decision_and_revocation_serialize_at_publication_boundary(
    client, db, guest_approval_context, monkeypatch, first
):
    """両順を実DBの待機で再現し、失効後の判断を保存しない。"""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event

    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services.publication_access_context import PublicationAccessContext
    from app.services.shared_fixed_approval import SharedFixedApprovalService

    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    other = TestClient(create_app())
    guest_login(other, db, ctx)
    path = approval_path(ctx, row)
    proof = other.post(path + "/decision-preview").json()["review_proof"]
    acquired, release = Event(), Event()
    if first == "decision":
        original = SharedFixedApprovalService.get
        calls = [0]

        def wait_get(self, *args, **kwargs):
            value = original(self, *args, **kwargs)
            if kwargs.get("write"):
                calls[0] += 1
                if calls[0] == 2:
                    acquired.set()
                    assert release.wait(10)
            return value

        monkeypatch.setattr(SharedFixedApprovalService, "get", wait_get)
    else:
        original = PublicationAccessContext.transaction

        @contextmanager
        def wait_transaction(self, *args, **kwargs):
            with original(self, *args, **kwargs) as value:
                yield value
                if kwargs.get("write") and len(args) >= 3 and args[2] is not None:
                    acquired.set()
                    assert release.wait(10)

        monkeypatch.setattr(PublicationAccessContext, "transaction", wait_transaction)

    def judge():
        return other.post(
            path + "/decisions",
            json=dict(
                version=1,
                operation_id=str(uuid4()),
                review_proof=proof,
                decision="approve",
                confirm_fixed_scope=True,
                confirm_changed_or_unknown=True,
            ),
        )

    def revoke():
        return client.post(
            f"/projects/{ctx['project'].id}/artifact-publications/{ctx['publication_id']}/grants/{ctx['grant']['id']}/revoke",
            json=dict(request_id=str(uuid4()), version=1, reason="競合での失効"),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        leading = pool.submit(judge if first == "decision" else revoke)
        assert acquired.wait(10)
        trailing = pool.submit(revoke if first == "decision" else judge)
        release.set()
        before, after = leading.result(timeout=15), trailing.result(timeout=15)
    assert before.status_code == 200, before.text
    assert after.status_code == (200 if first == "decision" else 404), after.text
    db.expire_all()
    assert db.get(FixedApprovalRequest, UUID(row["id"])).status == (
        "approved" if first == "decision" else "requested"
    )
    assert other.get(path).status_code == 404
    other.close()


@pytest.mark.parametrize("first", ["decision", "cancel"])
def test_cancel_and_decision_have_one_terminal_event(
    client, db, guest_approval_context, monkeypatch, first
):
    """取消と本人判断の両順で、後着の判断を上書きしない。"""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.repositories.fixed_approval import FixedApprovalRepository
    from app.services.shared_fixed_approval import SharedFixedApprovalService

    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    other = TestClient(create_app())
    guest_login(other, db, ctx)
    path = approval_path(ctx, row)
    proof = other.post(path + "/decision-preview").json()["review_proof"]
    acquired, release = Event(), Event()
    if first == "decision":
        original = SharedFixedApprovalService.get
        calls = [0]

        def hold(self, *args, **kwargs):
            value = original(self, *args, **kwargs)
            if kwargs.get("write"):
                calls[0] += 1
                if calls[0] == 2:
                    acquired.set()
                    assert release.wait(10)
            return value

        monkeypatch.setattr(SharedFixedApprovalService, "get", hold)
    else:
        original = FixedApprovalRepository.get

        def hold(self, *args, **kwargs):
            value = original(self, *args, **kwargs)
            if kwargs.get("lock"):
                acquired.set()
                assert release.wait(10)
            return value

        monkeypatch.setattr(FixedApprovalRepository, "get", hold)

    def judge():
        return other.post(
            path + "/decisions",
            json=dict(
                version=1,
                operation_id=str(uuid4()),
                review_proof=proof,
                decision="approve",
                confirm_fixed_scope=True,
                confirm_changed_or_unknown=True,
            ),
        )

    def cancel():
        return client.post(
            f"/projects/{ctx['project'].id}/fixed-approvals/{row['id']}/cancel",
            json=dict(version=1, operation_id=str(uuid4()), reason="競合時の取消"),
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            leading = pool.submit(judge if first == "decision" else cancel)
            assert acquired.wait(10)
            trailing = pool.submit(cancel if first == "decision" else judge)
            release.set()
            before, after = leading.result(timeout=15), trailing.result(timeout=15)
        assert before.status_code == 200, before.text
        assert after.status_code == 409, after.text
        db.expire_all()
        assert db.get(FixedApprovalRequest, UUID(row["id"])).status == (
            "approved" if first == "decision" else "cancelled"
        )
        assert db.scalar(
            select(func.count()).select_from(FixedApprovalEvent).where(
                FixedApprovalEvent.approval_id == UUID(row["id"]),
                FixedApprovalEvent.seq == 2,
            )
        ) == 1
    finally:
        release.set()
        other.close()


def test_changed_child_content_requires_fresh_explicit_confirmation(
    client, db, guest_approval_context
):
    """文書rootの版が変わらない子要件更新も検知し、旧内容への確認を求める。"""
    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    guest_login(client, db, ctx)
    path = approval_path(ctx, row)
    preview = client.post(path + "/decision-preview").json()
    assert preview["body_comparison"] == "equal"
    ctx["req"].description = "未公開の現在本文"
    ctx["req"].version += 1
    db.commit()
    current = client.post(path + "/decision-preview")
    assert current.status_code == 200 and current.json()["body_comparison"] == "changed"
    assert "未公開の現在本文" not in current.text
    data = dict(
        version=1,
        operation_id=str(uuid4()),
        decision="approve",
        confirm_fixed_scope=True,
        review_proof=current.json()["review_proof"],
    )
    assert client.post(path + "/decisions", json=data).status_code == 409
    stale = {
        **data,
        "confirm_changed_or_unknown": True,
        "review_proof": preview["review_proof"],
    }
    assert client.post(path + "/decisions", json=stale).status_code == 409
    assert (
        client.post(
            path + "/decisions", json={**data, "confirm_changed_or_unknown": True}
        ).status_code
        == 200
    )


@pytest.mark.parametrize("state", ["session_revoked", "mfa_changed", "source_deleted"])
def test_current_qualification_is_rechecked_before_decision(
    client, db, guest_approval_context, state
):
    """確認後のsession/MFA/元sourceの失効を無視しない。"""
    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    sid = guest_login(client, db, ctx)
    path = approval_path(ctx, row)
    proof = client.post(path + "/decision-preview").json()["review_proof"]
    if state == "session_revoked":
        db.get(UserSession, sid).revoked_at = datetime.now(UTC)
    elif state == "mfa_changed":
        ctx["guests"][0][0].mfa_security_version += 1
    else:
        ctx["req"].deleted_at = datetime.now(UTC)
    db.commit()
    result = client.post(
        path + "/decisions",
        json=dict(
            version=1,
            operation_id=str(uuid4()),
            review_proof=proof,
            decision="approve",
            confirm_fixed_scope=True,
        ),
    )
    assert result.status_code in (401, 403, 404), result.text
    db.expire_all()
    assert db.get(FixedApprovalRequest, UUID(row["id"])).status == "requested"
    assert db.scalar(select(func.count()).select_from(FixedApprovalEvent)) == 1


def test_new_validation_does_not_reflect_notes_or_proofs_and_csrf_is_required(
    client, db, guest_approval_context
):
    """エラーへ入力値を反射せず、更新には既存CSRFを要求する。"""
    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    guest_login(client, db, ctx)
    path = approval_path(ctx, row)
    result = client.post(
        path + "/decisions",
        json={"review_proof": "private-proof-value", "comment": "private-reason-value"},
    )
    assert result.status_code == 422
    assert (
        "private-proof-value" not in result.text
        and "private-reason-value" not in result.text
    )
    assert result.headers["cache-control"] == "private, no-store"
    client.headers.pop(settings.csrf_header_name)
    denied = client.post(path + "/decision-preview")
    assert denied.status_code == 403 and denied.json()["code"] == "CSRF_TOKEN_INVALID"


def test_new_operation_stop_keeps_authorized_history_and_internal_cancel(
    client, db, guest_approval_context, monkeypatch
):
    """新操作停止時にも既存共有readerと現在許可された履歴を維持する。"""
    ctx = guest_approval_context
    row, _ = create_request(client, ctx)
    monkeypatch.setattr(settings, "guest_fixed_approvals_enabled", False)
    guest_login(client, db, ctx)
    path = approval_path(ctx, row)
    detail = client.get(path)
    assert detail.status_code == 200 and detail.json()["can_decide"] is False
    assert client.post(path + "/decision-preview").status_code == 403
    assert (
        client.get(f"/shared/publications/{ctx['publication_id']}").status_code == 200
    )
    authorize_as(client, ctx["user"])
    cancelled = client.post(
        f"/projects/{ctx['project'].id}/fixed-approvals/{row['id']}/cancel",
        json=dict(version=1, operation_id=str(uuid4()), reason="停止中に取消"),
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
