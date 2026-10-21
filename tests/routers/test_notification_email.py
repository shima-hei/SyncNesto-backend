"""実DBで通知の正本・本人設定・配送の境界を確認する。"""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text

from app.core.config import settings
from app.models.notification import Notification
from app.models.notification_email import NotificationEmailAttempt as Attempt
from app.models.notification_email import NotificationEmailDelivery as Delivery
from app.models.notification_email import NotificationEmailPreference as Preference
from app.models.rbac import Permission, Role, RolePermission
from app.services.notification_email_delivery import ApprovalMailWorker
from app.services.notification_email_transport import MailOutcome
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_releases import context as context  # noqa: F401
from tests.routers.test_fixed_approvals import approval_context as approval_context  # noqa: F401
from tests.routers.test_fixed_approvals import decide, request
from tests.routers.test_guest_fixed_approvals import (
    approval_path,
    create_request,
    guest_login,
)
from tests.routers.test_guest_fixed_approvals import (
    guest_approval_context as guest_approval_context,  # noqa: F401
)
from tests.routers.test_publication_access import access_fixture as access_fixture  # noqa: F401


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    """新しい明示ダミーだけを使い、実メールへ接続しない。"""
    monkeypatch.setattr(settings, "fixed_approval_email_enabled", True)
    monkeypatch.setattr(settings, "fixed_approval_notifications_enabled", True)
    monkeypatch.setattr(settings, "frontend_public_url", "http://localhost:3000")
    monkeypatch.setattr(settings, "local_email_capture_host", "127.0.0.1")
    monkeypatch.setattr(settings, "local_email_capture_port", 1025)


def prepare(client, db, ctx, monkeypatch, users=None):
    """ダミーの通知先を本人Userへ結び付ける。"""
    users = users or [ctx["user"], ctx["approver"]]
    for user in users:
        user.email = f"ux-mail-{user.id}@example.com"
    db.commit()
    monkeypatch.setattr(
        settings, "local_email_capture_recipients", ",".join(u.email for u in users)
    )
    for user in users:
        authorize_as(client, user)
        client.headers["X-Syncnesto-Expected-User"] = str(user.id)
        response = client.patch(
            "/notification-preferences",
            json={"fixed_approval_email_enabled": True, "version": 0},
        )
        assert response.status_code == 200, response.text
    authorize_as(client, ctx["user"])
    client.headers["X-Syncnesto-Expected-User"] = str(ctx["user"].id)


def worker(db, outcome="accepted"):
    """配送adapterだけをfakeとし、DB・権限・正本は実物を使う。"""
    messages = []

    def send(message, guard, expiry):
        messages.append(message)
        return MailOutcome(
            outcome, "test_receipt", "opaque-receipt" if outcome == "accepted" else None
        )

    return ApprovalMailWorker(db.get_bind(), SimpleNamespace(send=send)), messages


def only_delivery(db):
    """別接続の更新を新しく読み直す。"""
    db.expire_all()
    return db.scalars(select(Delivery)).one()


def test_default_opt_in_version_and_no_recipient_injection(
    client, db, approval_context, monkeypatch
):
    """初期OFF、GET無書込み、本人選択と初回競合を確認する。"""
    ctx = approval_context
    response = client.get("/notification-preferences")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {
        "fixed_approval_email_enabled": False,
        "version": 0,
        "available": False,
        "pause_reason": None,
    }
    assert db.scalar(select(func.count()).select_from(Preference)) == 0
    assert (
        client.patch(
            "/notification-preferences",
            json={
                "fixed_approval_email_enabled": True,
                "version": 0,
                "user_id": ctx["approver"].id,
            },
        ).status_code
        == 422
    )
    prepare(client, db, ctx, monkeypatch)
    replay = client.patch(
        "/notification-preferences",
        json={"fixed_approval_email_enabled": True, "version": 0},
    )
    assert replay.status_code == 409
    assert replay.json()["current"]["version"] == 1
    authorize_as(client, ctx["approver"])
    mismatch = client.patch(
        "/notification-preferences",
        json={"fixed_approval_email_enabled": False, "version": 1},
    )
    assert mismatch.status_code == 403
    assert db.get(Preference, ctx["approver"].id).enabled
    assert mismatch.headers["referrer-policy"] == "no-referrer"
    monkeypatch.setattr(settings, "mfa_required", True)
    assert client.get("/notification-preferences").status_code == 403


def test_internal_event_delivery_and_no_backfill(
    client, db, approval_context, monkeypatch
):
    """既存通知を後からメール化せず、新規IDだけ一度送る。"""
    ctx = approval_context
    old, old_payload = request(client, ctx)
    prepare(client, db, ctx, monkeypatch)
    assert client.post(ctx["approvals"], json=old_payload).status_code == 200
    assert db.scalar(select(func.count()).select_from(Delivery)) == 0
    # 同じ固定対象を同じ相手へ二重申請せず、先に旧依頼を取り消す。
    response = client.post(
        f"{ctx['approvals']}/{old['id']}/cancel",
        json={
            "version": old["version"],
            "operation_id": str(uuid4()),
            "reason": "test",
        },
    )
    assert response.status_code == 200, response.text
    # 取消メールは別eventとして生成される。
    before = set(db.scalars(select(Delivery.id)))
    row, payload = request(client, ctx)
    assert client.post(ctx["approvals"], json=payload).status_code == 200
    mail_worker, messages = worker(db)
    for _ in range(len(before) + 1):
        assert mail_worker.run_once() == {"accepted": 1}
    assert mail_worker.run_once() == {"idle": 1}
    assert len(messages) == len(before) + 1
    assert all("http://localhost:3000/notifications" in m.text for m in messages)
    assert all(
        ctx["user"].name not in m.text and "申請理由" not in m.text for m in messages
    )
    assert db.scalar(select(func.count()).select_from(Notification)) >= 3
    assert row["status"] == "requested"


@pytest.mark.parametrize("change", ["off_on", "email", "permission", "decided"])
def test_pending_delivery_reauthorizes_and_never_revives(
    client, db, approval_context, monkeypatch, change
):
    """ON時の資格を配送時まで信用せず、旧便を復活させない。"""
    ctx = approval_context
    prepare(client, db, ctx, monkeypatch)
    row, _ = request(client, ctx)
    if change == "off_on":
        authorize_as(client, ctx["approver"])
        client.headers["X-Syncnesto-Expected-User"] = str(ctx["approver"].id)
        assert (
            client.patch(
                "/notification-preferences",
                json={"fixed_approval_email_enabled": False, "version": 1},
            ).status_code
            == 200
        )
        assert (
            client.patch(
                "/notification-preferences",
                json={"fixed_approval_email_enabled": True, "version": 2},
            ).status_code
            == 200
        )
    elif change == "email":
        ctx["approver"].email = "ux-mail-new@example.com"
        db.commit()
    elif change == "permission":
        role = db.scalar(
            select(Role.id).where(Role.key == "project_admin", Role.scope == "project")
        )
        permission = db.scalar(
            select(Permission.id).where(Permission.code == "requirement:read")
        )
        db.execute(
            delete(RolePermission).where(
                RolePermission.role_id == role,
                RolePermission.permission_id == permission,
            )
        )
        db.commit()
    else:
        authorize_as(client, ctx["approver"])
        decide(client, ctx, row)
    mail_worker, messages = worker(db)
    assert mail_worker.run_once() == {"suppressed": 1}
    assert not messages


@pytest.mark.parametrize(
    "outcome,state",
    [("temporary", "retry_wait"), ("failed", "failed"), ("unknown", "unknown")],
)
def test_failure_classification_and_terminal_unknown(
    client, db, approval_context, monkeypatch, outcome, state
):
    """明確な一時失敗だけ再試行し、業務判断を失わない。"""
    ctx = approval_context
    prepare(client, db, ctx, monkeypatch)
    row, _ = request(client, ctx)
    mail_worker, messages = worker(db, outcome)
    assert mail_worker.run_once() == {outcome: 1}
    saved = only_delivery(db)
    assert saved.state == state
    assert saved.attempt_count == 1
    assert mail_worker.run_once() == {"idle": 1}
    assert len(messages) == 1
    assert client.get(f"{ctx['approvals']}/{row['id']}").json()["status"] == "requested"
    assert db.scalar(select(func.count()).select_from(Attempt)) == 2


def test_recovery_fencing_and_unknown_no_resend(
    client, db, approval_context, monkeypatch
):
    """送信前のlease失効と、開始耐久化後の失効を区別する。"""
    ctx = approval_context
    prepare(client, db, ctx, monkeypatch)
    request(client, ctx)
    mail_worker, messages = worker(db)
    old = mail_worker.recover_and_claim()
    assert old is not None
    with mail_worker.sessions.begin() as control:
        saved = control.get(Delivery, old.id)
        assert saved is not None
        saved.lease_until = control.execute(
            select(func.clock_timestamp())
        ).scalar_one() - timedelta(seconds=1)
    new = mail_worker.recover_and_claim()
    assert new is not None
    assert old.claim_token != new.claim_token
    assert mail_worker.deliver(old).outcome == "suppressed"
    assert not messages
    with mail_worker.sessions.begin() as control:
        saved = control.get(Delivery, new.id)
        assert saved is not None
        saved.state, saved.attempt_count = "sending", 1
        mail_worker.record(control, saved, "started")
        saved.lease_until = control.execute(
            select(func.clock_timestamp())
        ).scalar_one() - timedelta(seconds=1)
    assert mail_worker.run_once() == {"idle": 1}
    assert only_delivery(db).state == "unknown"
    assert not messages


def test_new_hook_failure_rolls_back_business(
    client, db, approval_context, monkeypatch
):
    """待ち行だけ欠落する成功を作らない。"""
    ctx = approval_context
    prepare(client, db, ctx, monkeypatch)

    def fail(*args):
        raise RuntimeError("test queue unavailable")

    monkeypatch.setattr(
        "app.services.notification_email_delivery.enqueue_notification_email", fail
    )
    from app.models.fixed_approval import FixedApprovalRequest

    with pytest.raises(RuntimeError, match="test queue unavailable"):
        request(client, ctx)
    assert db.scalar(select(func.count()).select_from(FixedApprovalRequest)) == 0
    assert db.scalar(select(func.count()).select_from(Notification)) == 0


def test_feature_off_and_worker_reject_remote(
    db, client, approval_context, monkeypatch
):
    """public/遠隔DBや停止中に新経路を開かない。"""
    monkeypatch.setattr(settings, "fixed_approval_email_enabled", False)
    assert client.get("/notification-preferences").status_code == 404
    mail_worker, _ = worker(db)
    assert mail_worker.run_once() == {"disabled": 1}
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(ValueError, match="local_database_required"):
        mail_worker.run_once()


@pytest.mark.parametrize(
    "kind", ["requirement", "requirement_document", "specification", "test_design"]
)
def test_external_fixed_title_and_current_generation(
    client, db, guest_approval_context, monkeypatch, kind
):
    """全4種は公開済み名称だけを使い、外部の本人設定も組織なしで操作する。"""
    ctx = guest_approval_context
    guest = ctx["guests"][0][0]
    prepare(client, db, ctx, monkeypatch, users=[ctx["user"], guest])
    if kind == "test_design":
        from uuid import UUID

        from app.models.artifact_publication import ArtifactPublicationNode
        from tests.routers.test_artifact_publications import create
        from tests.routers.test_artifact_test_design import freeze
        from tests.routers.test_publication_access import publish
        from tests.routers.test_test_designs import graph

        base = f"/projects/{ctx['project'].id}/test-designs"
        blank = client.post(base, json={"name": "外部確認するテスト設計"})
        assert blank.status_code == 201, blank.text
        design = client.put(base + f"/{blank.json()['id']}", json=graph(blank.json()))
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
                    ArtifactPublicationNode.publication_id
                    == UUID(ctx["publication_id"])
                )
            )
        )
        ctx["grant"] = client.get(
            f"/projects/{ctx['project'].id}/artifact-publications/"
            f"{ctx['publication_id']}/grants"
        ).json()["items"][0]
    created, body = create_request(client, ctx, kind)
    assert client.post(ctx["requests"], json=body).status_code == 200
    mail_worker, messages = worker(db)
    assert mail_worker.run_once() == {"accepted": 1}
    assert len(messages) == 1 and messages[0].to == guest.email
    assert "/shared/notifications" in messages[0].text
    assert ctx["project"].name not in messages[0].text
    assert ctx["user"].name not in messages[0].text
    assert "外部合意不足" not in messages[0].text
    guest_login(client, db, ctx)
    assert client.get(approval_path(ctx, created)).status_code == 200


def test_external_revocation_suppresses_pending_mail(
    client, db, guest_approval_context, monkeypatch
):
    """公開停止後は本文だけでなく名称の新規送信も止まる。"""
    ctx = guest_approval_context
    guest = ctx["guests"][0][0]
    prepare(client, db, ctx, monkeypatch, users=[ctx["user"], guest])
    create_request(client, ctx)
    path = (
        f"/projects/{ctx['project'].id}/artifact-publications/"
        f"{ctx['publication_id']}/grants/{ctx['grant']['id']}/revoke"
    )
    result = client.post(
        path, json=dict(request_id=str(uuid4()), version=1, reason="公開を終了")
    )
    assert result.status_code == 200, result.text
    mail_worker, messages = worker(db)
    assert mail_worker.run_once() == {"suppressed": 1}
    assert not messages


def test_unknown_resolution_requires_positive_evidence(
    client, db, approval_context, monkeypatch
):
    """Mailpitにないことだけで再送せず、根拠・世代・履歴を保持する。"""
    import hashlib

    ctx = approval_context
    prepare(client, db, ctx, monkeypatch)
    request(client, ctx)
    mail_worker, _ = worker(db, "unknown")
    assert mail_worker.run_once() == {"unknown": 1}
    row = only_delivery(db)
    row.lease_until = db.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
    db.commit()
    evidence: dict = {
        "attempt_id": str(row.claim_token),
        "message_id": "<"
        + hashlib.sha256(f"approval-mail/{row.id}".encode()).hexdigest()
        + "@example.com>",
        "basis": "absent_from_mailpit",
    }
    with pytest.raises(ValueError, match="positive_nonacceptance"):
        mail_worker.resolve(row.id, row.version, False, "受信一覧だけを確認", evidence)
    assert only_delivery(db).state == "unknown"
    evidence.update(basis="smtp_rejection", smtp_code=451, reply_received=True)
    version = row.version
    mail_worker.resolve(row.id, version, False, "SMTPの明示拒否を確認", evidence)
    assert only_delivery(db).state == "retry_wait"
    assert (
        db.scalar(
            select(func.count())
            .select_from(Attempt)
            .where(Attempt.record_kind == "resolution")
        )
        == 1
    )
    with pytest.raises(ValueError, match="unknown_finished_version"):
        mail_worker.resolve(row.id, version, False, "同じ確認の再送", evidence)


def test_send_fences_setting_off_and_permission_delete(
    client, db, approval_context, monkeypatch
):
    """送信中は既存資格とOFFを待たせ、独立制御接続で自己deadlockしない。"""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy.exc import OperationalError

    ctx = approval_context
    prepare(client, db, ctx, monkeypatch)
    request(client, ctx)
    sending, release = Event(), Event()

    def send(message, guard, expiry):
        sending.set()
        assert release.wait(5)
        return MailOutcome("accepted", "test_receipt")

    mail_worker = ApprovalMailWorker(db.get_bind(), SimpleNamespace(send=send))
    role = db.scalar(
        select(Role.id).where(Role.key == "project_admin", Role.scope == "project")
    )
    permission = db.scalar(
        select(Permission.id).where(Permission.code == "requirement:read")
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(mail_worker.run_once)
            assert sending.wait(5)
            assert ApprovalMailWorker(db.get_bind()).run_once() == {"busy": 1}
            for sql, parameters in [
                (
                    "UPDATE notification_email_preferences SET enabled=false "
                    "WHERE user_id=:identity",
                    {"identity": ctx["approver"].id},
                ),
                (
                    "DELETE FROM role_permissions WHERE role_id=:role "
                    "AND permission_id=:permission",
                    {"role": role, "permission": permission},
                ),
            ]:
                with pytest.raises(OperationalError):
                    with mail_worker.sessions.begin() as control:
                        control.execute(text("SET LOCAL lock_timeout='150ms'"))
                        control.execute(text(sql), parameters)
            release.set()
            assert future.result(timeout=5) == {"accepted": 1}
    finally:
        release.set()
    assert only_delivery(db).state == "accepted"
