"""メール本人確認の公開API、組織権限、失効、配送失敗の回帰検証。"""

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Lock

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.exceptions import EmailUnavailableError
from app.core.security import get_password_hash, verify_password
from app.models.account_action import AccountAction
from app.models.audit_log import AuditLog
from app.models.login_attempt import LoginAttempt
from app.models.rbac import Role
from app.models.request_limit import RequestLimit
from app.models.session import UserSession
from app.models.tenant import Tenant, TenantMember
from app.services.account_action import AccountActionService
from app.services.email import EmailService, OutgoingEmail
from tests.fakes.storage import FakeStorageService
from tests.helpers.auth import authorize_as, create_session_token

NEW_PASSWORD = "New-password-for-2026!"


class FakeEmailService(EmailService):
    """秘密をテストプロセス内だけに保持し、外部配信を行わない。"""

    def __init__(self) -> None:
        """配送メッセージと失敗モードを準備する。"""
        self.messages: list[OutgoingEmail] = []
        self.available = True
        self.fail_send = False
        self.lock = Lock()

    def ensure_available(self) -> None:
        """無効な配信設定を模擬する。"""
        if not self.available:
            raise EmailUnavailableError()

    def send(self, message: OutgoingEmail) -> str:
        """成功したメールだけ保存し、固定形式の受付IDを返す。"""
        self.ensure_available()
        if self.fail_send:
            raise EmailUnavailableError()
        with self.lock:
            self.messages.append(message)
            return f"fake-mail-{len(self.messages)}"


@pytest.fixture(autouse=True)
def mail(monkeypatch):
    """公開・組織管理APIの送信アダプターを共通fakeへ差し替える。"""
    from app.routers import account_actions, auth, tenants

    fake = FakeEmailService()
    service = AccountActionService(email_service=fake)
    monkeypatch.setattr(account_actions, "service", service)
    monkeypatch.setattr(tenants, "account_action_service", service)
    monkeypatch.setattr(auth, "storage_service", FakeStorageService())
    monkeypatch.setattr(settings, "frontend_public_url", "http://localhost:3000")
    monkeypatch.setattr(settings, "account_action_expire_seconds", 1800)
    return fake


def _token(message: OutgoingEmail) -> str:
    """メールのfragmentからトークンを取り出す。ログには出さない。"""
    match = re.search(r"#token=([A-Za-z0-9_-]{32,128})", message.text)
    assert match is not None
    return match.group(1)


def _request_reset(client, user, mail) -> str:
    """再設定を申請し、fakeの受信メールからトークンを取得する。"""
    previous = len(mail.messages)
    response = client.post("/auth/password-reset/request", json={"email": user.email})
    assert response.status_code == 202
    assert len(mail.messages) == previous + 1
    assert mail.messages[-1].to == user.email
    token = _token(mail.messages[-1])
    assert token not in response.text
    return token


def _request_email_change(client, new_email, mail) -> str:
    """本人の変更申請から旧メールへの承認トークンを得る。"""
    response = client.post("/auth/email-change/request", json={"new_email": new_email})
    assert response.status_code == 202
    token = _token(mail.messages[-1])
    assert token not in response.text
    return token


def _action(db, token: str) -> AccountAction:
    """DBへ保存されるのはハッシュのみであることを前提に行を読む。"""
    digest = hashlib.sha256(token.encode()).hexdigest()
    return db.query(AccountAction).filter(AccountAction.token_hash == digest).one()


def _assert_invalid(response) -> None:
    """存在・用途・期限・権限の違いを同じ公開エラーにする。"""
    assert response.status_code == 400
    assert response.json()["code"] == "ACCOUNT_ACTION_INVALID"


def test_forgot_responses_do_not_disclose_identity_existence(
    client, db, create_test_user, mail
):
    """有効・無効・削除済み・不在のアドレスに同じ案内を返す。"""
    active = create_test_user(email="active-reset@example.com")
    inactive = create_test_user(email="inactive-reset@example.com")
    deleted = create_test_user(email="deleted-reset@example.com")
    inactive.is_active = False
    deleted.deleted_at = datetime.now(UTC)
    db.commit()

    responses = [
        client.post("/auth/password-reset/request", json={"email": email})
        for email in (
            active.email,
            inactive.email,
            deleted.email,
            "missing-reset@example.com",
        )
    ]
    assert all(response.status_code == 202 for response in responses)
    assert all(response.json() == responses[0].json() for response in responses)
    assert [message.to for message in mail.messages] == [active.email]
    assert db.query(AccountAction).count() == 1


def test_request_and_inspection_do_not_change_credentials_or_consume_link(
    client, db, create_test_user, mail
):
    """申請・表示・GETだけでは変更や全ログイン失効が発生しない。"""
    user = create_test_user(email="inspect-reset@example.com")
    session_id = authorize_as(client, user)
    old_hash, version = user.hashed_password, user.version
    token = _request_reset(client, user, mail)
    for _ in range(3):
        response = client.post("/auth/account-actions/inspect", json={"token": token})
        assert response.status_code == 200
        assert response.json()["purpose"] == "password_reset"
        assert token not in response.text
    assert client.get("/auth/password-reset/confirm").status_code == 405
    db.expire_all()
    assert user.hashed_password == old_hash
    assert user.version == version
    assert db.get(UserSession, session_id).revoked_at is None
    assert _action(db, token).consumed_at is None
    assert len(mail.messages) == 1


def test_password_reset_revokes_all_sessions_and_links_and_clears_login_lock(
    client, db, create_test_user, mail, caplog
):
    """未ログインの本人の確定だけで変更し、他リンクとログインを原子的に失効する。"""
    user = create_test_user(email="complete-reset@example.com")
    first_session = authorize_as(client, user)
    old_csrf = client.cookies.get(settings.csrf_cookie_name)
    _, second_session = create_session_token(user)
    first_token = _request_reset(client, user, mail)
    second_token = _request_reset(client, user, mail)
    db.add(
        LoginAttempt(
            email=user.email,
            failed_count=5,
            locked_until=datetime.now(UTC) + timedelta(minutes=30),
            last_failed_at=datetime.now(UTC),
        )
    )
    db.commit()
    client.cookies.clear()

    response = client.post(
        "/auth/password-reset/confirm",
        json={"token": first_token, "password": NEW_PASSWORD},
    )
    assert response.status_code == 200
    assert NEW_PASSWORD not in response.text
    assert first_token not in response.text
    db.expire_all()
    assert verify_password(NEW_PASSWORD, user.hashed_password)
    for session_id in (first_session, second_session):
        session = db.get(UserSession, session_id)
        assert session.revoked_at is not None
        assert session.revoked_reason == "credentials_changed"
    assert _action(db, first_token).consumed_at is not None
    assert _action(db, second_token).revoked_at is not None
    attempt = db.query(LoginAttempt).filter(LoginAttempt.email == user.email).one()
    assert attempt.failed_count == 0
    assert attempt.locked_until is None
    _assert_invalid(
        client.post(
            "/auth/password-reset/confirm",
            json={"token": first_token, "password": "Replay-password-for-2026!"},
        )
    )
    _assert_invalid(
        client.post("/auth/account-actions/inspect", json={"token": second_token})
    )

    saved_actions = [
        {
            column.key: getattr(action, column.key)
            for column in AccountAction.__table__.columns
        }
        for action in db.query(AccountAction).all()
    ]
    audit = db.query(AuditLog).all()
    persisted = json.dumps(
        {"actions": saved_actions, "audit": [row.extra_metadata for row in audit]},
        default=str,
    )
    for secret in (first_token, second_token, NEW_PASSWORD):
        assert secret not in persisted
        assert secret not in caplog.text
    assert any(row.event_type == "account.password_reset.completed" for row in audit)
    assert all("#token=" not in message.text for message in mail.messages[2:])

    assert (
        client.post(
            "/auth/login", json={"email": user.email, "password": "password123"}
        ).status_code
        == 401
    )
    login = client.post(
        "/auth/login", json={"email": user.email, "password": NEW_PASSWORD}
    )
    assert login.status_code == 200
    assert login.cookies.get(settings.csrf_cookie_name)
    assert login.cookies.get(settings.csrf_cookie_name) != old_csrf


def test_short_password_does_not_consume_reset_link(client, db, create_test_user, mail):
    """12文字未満のパスワードは入力検証で拒否し、リンクを残す。"""
    user = create_test_user(email="short-password@example.com")
    old_hash = user.hashed_password
    token = _request_reset(client, user, mail)
    response = client.post(
        "/auth/password-reset/confirm", json={"token": token, "password": "short"}
    )
    assert response.status_code == 422
    db.expire_all()
    assert user.hashed_password == old_hash
    assert _action(db, token).consumed_at is None
    assert (
        client.post("/auth/account-actions/inspect", json={"token": token}).status_code
        == 200
    )


@pytest.mark.parametrize(
    "state", ["expired", "revoked", "unsent", "password_changed", "inactive", "deleted"]
)
def test_stale_reset_links_share_one_error_and_cannot_change_user(
    client, db, create_test_user, mail, state
):
    """期限・送信状態・Identity状態が無効なら同じ公開エラーで拒否する。"""
    user = create_test_user(email="stale-reset@example.com")
    token = _request_reset(client, user, mail)
    action = _action(db, token)
    if state == "expired":
        action.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif state == "revoked":
        action.revoked_at = datetime.now(UTC)
    elif state == "unsent":
        action.sent_at = None
    elif state == "password_changed":
        user.hashed_password = get_password_hash("Changed-outside-flow-2026!")
    elif state == "inactive":
        user.is_active = False
    else:
        user.deleted_at = datetime.now(UTC)
    db.commit()
    old_hash = user.hashed_password
    _assert_invalid(client.post("/auth/account-actions/inspect", json={"token": token}))
    _assert_invalid(
        client.post(
            "/auth/password-reset/confirm",
            json={"token": token, "password": NEW_PASSWORD},
        )
    )
    db.expire_all()
    assert user.hashed_password == old_hash
    assert action.consumed_at is None


def test_wrong_purpose_and_unknown_tokens_cannot_complete_another_operation(
    client, db, create_test_user, mail
):
    """再設定・旧メール承認・新メール確認のリンクを相互に流用できない。"""
    user = create_test_user(email="purpose-check@example.com")
    reset_token = _request_reset(client, user, mail)
    _assert_invalid(
        client.post("/auth/email-change/approve", json={"token": reset_token})
    )
    _assert_invalid(
        client.post("/auth/email-change/confirm", json={"token": reset_token})
    )
    _assert_invalid(
        client.post("/auth/account-actions/inspect", json={"token": "x" * 43})
    )
    authorize_as(client, user)
    email_token = _request_email_change(client, "purpose-new@example.com", mail)
    client.cookies.clear()
    _assert_invalid(
        client.post(
            "/auth/password-reset/confirm",
            json={"token": email_token, "password": NEW_PASSWORD},
        )
    )
    _assert_invalid(
        client.post("/auth/email-change/confirm", json={"token": email_token})
    )
    db.expire_all()
    assert user.email == "purpose-check@example.com"
    assert _action(db, reset_token).consumed_at is None
    assert _action(db, email_token).consumed_at is None


def test_email_change_requires_old_approval_and_new_confirmation(
    client, db, create_test_user, mail, caplog
):
    """両方のメールで本人が確定するまでIdentityを維持し、完了後に全セッションを失効する。"""
    user = create_test_user(email="old-address@example.com")
    old_hash = user.hashed_password
    session_id = authorize_as(client, user)
    old_token = _request_email_change(client, "new-address@example.com", mail)
    assert mail.messages[-1].to == "old-address@example.com"
    client.cookies.clear()
    assert (
        client.post("/auth/account-actions/inspect", json={"token": old_token}).json()[
            "purpose"
        ]
        == "email_change_approve"
    )
    _assert_invalid(
        client.post("/auth/email-change/confirm", json={"token": old_token})
    )
    db.expire_all()
    assert user.email == "old-address@example.com"
    assert db.get(UserSession, session_id).revoked_at is None

    approved = client.post("/auth/email-change/approve", json={"token": old_token})
    assert approved.status_code == 200
    assert mail.messages[-1].to == "new-address@example.com"
    new_token = _token(mail.messages[-1])
    inspected = client.post("/auth/account-actions/inspect", json={"token": new_token})
    assert inspected.status_code == 200
    assert inspected.json()["purpose"] == "email_change_verify"
    db.expire_all()
    assert user.email == "old-address@example.com"
    assert user.hashed_password == old_hash
    assert _action(db, old_token).consumed_at is not None
    assert _action(db, new_token).consumed_at is None
    assert db.get(UserSession, session_id).revoked_at is None

    confirmed = client.post("/auth/email-change/confirm", json={"token": new_token})
    assert confirmed.status_code == 200
    db.expire_all()
    assert user.email == "new-address@example.com"
    assert user.hashed_password == old_hash
    assert db.get(UserSession, session_id).revoked_at is not None
    _assert_invalid(
        client.post("/auth/email-change/approve", json={"token": old_token})
    )
    _assert_invalid(
        client.post("/auth/email-change/confirm", json={"token": new_token})
    )
    assert [message.to for message in mail.messages[-2:]] == [
        "old-address@example.com",
        "new-address@example.com",
    ]
    assert all("#token=" not in message.text for message in mail.messages[-2:])
    audit = json.dumps([row.extra_metadata for row in db.query(AuditLog)], default=str)
    for token in (old_token, new_token):
        assert token not in audit + caplog.text + confirmed.text
    login = client.post(
        "/auth/login", json={"email": user.email, "password": "password123"}
    )
    assert login.status_code == 200


def test_second_step_mail_failure_preserves_old_link_for_retry(
    client, db, create_test_user, mail
):
    """新メールへの配信が失敗しても旧リンクを消費せず、承認操作をやり直せる。"""
    user = create_test_user(email="retry-old@example.com")
    authorize_as(client, user)
    old_token = _request_email_change(client, "retry-new@example.com", mail)
    client.cookies.clear()
    mail.fail_send = True
    response = client.post("/auth/email-change/approve", json={"token": old_token})
    assert response.status_code == 503
    assert response.json()["code"] == "EMAIL_UNAVAILABLE"
    db.expire_all()
    assert user.email == "retry-old@example.com"
    assert _action(db, old_token).consumed_at is None
    assert db.query(AccountAction).count() == 1
    assert (
        client.post(
            "/auth/account-actions/inspect", json={"token": old_token}
        ).status_code
        == 200
    )
    mail.fail_send = False
    assert (
        client.post("/auth/email-change/approve", json={"token": old_token}).status_code
        == 200
    )
    new_token = _token(mail.messages[-1])
    assert (
        client.post("/auth/email-change/confirm", json={"token": new_token}).status_code
        == 200
    )
    db.refresh(user)
    assert user.email == "retry-new@example.com"


def test_public_delivery_failure_keeps_existing_usable_link_and_credentials(
    client, db, create_test_user, mail
):
    """後続申請の配信失敗で、既に届いている再設定リンクやログインを壊さない。"""
    user = create_test_user(email="delivery-failed@example.com")
    session_id = authorize_as(client, user)
    old_hash = user.hashed_password
    token = _request_reset(client, user, mail)
    mail.fail_send = True
    response = client.post("/auth/password-reset/request", json={"email": user.email})
    assert response.status_code == 202
    assert (
        client.post("/auth/account-actions/inspect", json={"token": token}).status_code
        == 200
    )
    db.expire_all()
    assert user.hashed_password == old_hash
    assert db.get(UserSession, session_id).revoked_at is None
    assert db.query(AccountAction).count() == 1
    assert any(
        row.event_type == "account.password_reset.send_failed"
        for row in db.query(AuditLog).all()
    )


def test_same_new_email_can_be_requested_but_only_one_identity_can_confirm(
    client, db, create_test_user, mail
):
    """未承認申請で他人のメールを予約せず、最初に確認したIdentityだけに割り当てる。"""
    first = create_test_user(email="first-old@example.com")
    second = create_test_user(email="second-old@example.com")
    destination = "shared-destination@example.com"
    with TestClient(client.app) as second_client:
        authorize_as(client, first)
        authorize_as(second_client, second)
        old_first = _request_email_change(client, destination, mail)
        old_second = _request_email_change(second_client, destination, mail)
        client.cookies.clear()
        second_client.cookies.clear()
        assert (
            client.post(
                "/auth/email-change/approve", json={"token": old_first}
            ).status_code
            == 200
        )
        new_first = _token(mail.messages[-1])
        assert (
            second_client.post(
                "/auth/email-change/approve", json={"token": old_second}
            ).status_code
            == 200
        )
        new_second = _token(mail.messages[-1])
        db.expire_all()
        assert first.email == "first-old@example.com"
        assert second.email == "second-old@example.com"
        assert (
            client.post(
                "/auth/email-change/confirm", json={"token": new_first}
            ).status_code
            == 200
        )
        response = second_client.post(
            "/auth/email-change/confirm", json={"token": new_second}
        )
    assert response.status_code == 400
    assert response.json()["code"] == "EMAIL_ALREADY_REGISTERED"
    db.expire_all()
    assert first.email == destination
    assert second.email == "second-old@example.com"
    assert _action(db, new_second).consumed_at is None


@pytest.fixture
def organization_users(db, create_test_user):
    """1組織のOwner/Admin/Memberと、他組織だけに属するIdentityを作る。"""
    users = {
        key: create_test_user(email=f"action-{key}@example.com")
        for key in ("owner", "admin", "member", "foreign")
    }
    tenant = db.query(Tenant).filter(Tenant.slug == "default").one()
    for key, user in users.items():
        member = (
            db.query(TenantMember)
            .filter(
                TenantMember.tenant_id == tenant.id, TenantMember.user_id == user.id
            )
            .one()
        )
        if key == "foreign":
            member.status = "removed"
        else:
            role_key = "tenant_" + (key if key in {"owner", "admin"} else "member")
            member.role_id = (
                db.query(Role)
                .filter(Role.key == role_key, Role.scope == "tenant")
                .one()
                .id
            )
    other = Tenant(name="Other", slug="action-other")
    db.add(other)
    db.flush()
    role_id = (
        db.query(Role)
        .filter(Role.key == "tenant_owner", Role.scope == "tenant")
        .one()
        .id
    )
    db.add(
        TenantMember(
            tenant_id=other.id,
            user_id=users["foreign"].id,
            role_id=role_id,
            display_name="Other Owner",
        )
    )
    db.commit()
    return users, tenant


def _member_request(client, target_id, operation):
    """組織内ユーザーの変更申請を行う。"""
    path = f"/tenants/current/members/{target_id}/{operation}"
    payload = (
        {"new_email": "member-new@example.com"} if operation == "email-change" else None
    )
    return client.post(path, json=payload)


@pytest.mark.parametrize("actor", ["owner", "admin"])
@pytest.mark.parametrize("operation", ["password-reset", "email-change"])
def test_tenant_owner_and_admin_can_request_for_active_member(
    client, db, organization_users, mail, actor, operation
):
    """Owner/Adminは現在の組織の有効Memberに本人確認を依頼できる。"""
    users, tenant = organization_users
    authorize_as(client, users[actor])
    client.headers["X-Tenant-ID"] = str(tenant.id)
    old_hash = users["member"].hashed_password
    response = _member_request(client, users["member"].id, operation)
    assert response.status_code == 202
    assert mail.messages[-1].to == users["member"].email
    token = _token(mail.messages[-1])
    db.expire_all()
    assert users["member"].hashed_password == old_hash
    action = _action(db, token)
    assert action.requested_by_id == users[actor].id
    assert action.tenant_id == tenant.id
    audits = (
        db.query(AuditLog).filter(AuditLog.target_user_id == users["member"].id).all()
    )
    assert any(row.tenant_id == tenant.id for row in audits)


@pytest.mark.parametrize("operation", ["password-reset", "email-change"])
@pytest.mark.parametrize(
    "target_state", ["foreign", "suspended", "inactive", "missing"]
)
def test_tenant_request_rejects_other_org_and_inactive_targets(
    client, db, organization_users, mail, operation, target_state
):
    """他組織のIdentity、停止済み所属・Identity、不在のIDを同じ権限エラーで拒否する。"""
    users, tenant = organization_users
    target = users["foreign"] if target_state == "foreign" else users["member"]
    if target_state == "suspended":
        db.query(TenantMember).filter(
            TenantMember.tenant_id == tenant.id, TenantMember.user_id == target.id
        ).one().status = "suspended"
    elif target_state == "inactive":
        target.is_active = False
    db.commit()
    authorize_as(client, users["owner"])
    client.headers["X-Tenant-ID"] = str(tenant.id)
    response = _member_request(
        client, 999999 if target_state == "missing" else target.id, operation
    )
    assert response.status_code == 403
    assert not mail.messages
    assert db.query(AccountAction).count() == 0


@pytest.mark.parametrize("actor", ["member", "admin"])
@pytest.mark.parametrize("operation", ["password-reset", "email-change"])
def test_member_cannot_manage_users_and_admin_cannot_request_owner_change(
    client, db, organization_users, mail, actor, operation
):
    """Memberには申請権限がなく、AdminはOwnerのログイン情報へ変更を依頼できない。"""
    users, tenant = organization_users
    authorize_as(client, users[actor])
    client.headers["X-Tenant-ID"] = str(tenant.id)
    response = _member_request(client, users["owner"].id, operation)
    assert response.status_code == 403
    assert not mail.messages
    assert db.query(AccountAction).count() == 0


@pytest.mark.parametrize("operation", ["password-reset", "email-change"])
def test_operator_without_tenant_management_role_cannot_request_member_change(
    client, db, organization_users, create_test_user, mail, operation
):
    """システム運営者のroleだけでは組織管理APIを使えない。"""
    users, tenant = organization_users
    operator = create_test_user(
        email="operator-action@example.com", system_role="system_admin"
    )
    membership = (
        db.query(TenantMember)
        .filter(
            TenantMember.tenant_id == tenant.id, TenantMember.user_id == operator.id
        )
        .one()
    )
    membership.role_id = (
        db.query(Role)
        .filter(Role.key == "tenant_member", Role.scope == "tenant")
        .one()
        .id
    )
    db.commit()
    authorize_as(client, operator)
    client.headers["X-Tenant-ID"] = str(tenant.id)
    assert _member_request(client, users["member"].id, operation).status_code == 403
    assert not mail.messages


@pytest.mark.parametrize(
    "state", ["actor_demoted", "actor_removed", "target_suspended", "tenant_suspended"]
)
@pytest.mark.parametrize("operation", ["password-reset", "email-change"])
def test_tenant_authority_is_rechecked_when_recipient_uses_link(
    client, db, organization_users, mail, state, operation
):
    """申請後の降格・所属取消し・停止を反映し、届いているリンクも拒否する。"""
    users, tenant = organization_users
    actor, target = users["admin"], users["member"]
    authorize_as(client, actor)
    client.headers["X-Tenant-ID"] = str(tenant.id)
    assert _member_request(client, target.id, operation).status_code == 202
    token = _token(mail.messages[-1])
    client.cookies.clear()
    if operation == "email-change":
        assert (
            client.post("/auth/email-change/approve", json={"token": token}).status_code
            == 200
        )
        token = _token(mail.messages[-1])
    actor_member = (
        db.query(TenantMember)
        .filter(TenantMember.tenant_id == tenant.id, TenantMember.user_id == actor.id)
        .one()
    )
    target_member = (
        db.query(TenantMember)
        .filter(TenantMember.tenant_id == tenant.id, TenantMember.user_id == target.id)
        .one()
    )
    if state == "actor_demoted":
        actor_member.role_id = (
            db.query(Role)
            .filter(Role.key == "tenant_member", Role.scope == "tenant")
            .one()
            .id
        )
    elif state == "actor_removed":
        actor_member.status = "removed"
    elif state == "target_suspended":
        target_member.status = "suspended"
    else:
        tenant.status = "suspended"
    db.commit()
    old_email, old_hash = target.email, target.hashed_password
    _assert_invalid(client.post("/auth/account-actions/inspect", json={"token": token}))
    path = (
        "/auth/email-change/confirm"
        if operation == "email-change"
        else "/auth/password-reset/confirm"
    )
    payload = {"token": token}
    if operation == "password-reset":
        payload["password"] = NEW_PASSWORD
    _assert_invalid(client.post(path, json=payload))
    db.expire_all()
    assert target.email == old_email
    assert target.hashed_password == old_hash
    assert _action(db, token).consumed_at is None


def test_unavailable_provider_returns_503_and_creates_no_link(
    client, db, create_test_user, mail
):
    """disabled相当の設定時は成功を装わず、既存Identityの状態を維持する。"""
    user = create_test_user(email="disabled-provider@example.com")
    old_hash = user.hashed_password
    mail.available = False
    response = client.post("/auth/password-reset/request", json={"email": user.email})
    assert response.status_code == 503
    assert response.json()["code"] == "EMAIL_UNAVAILABLE"
    assert not mail.messages
    assert db.query(AccountAction).count() == 0
    db.refresh(user)
    assert user.hashed_password == old_hash


def test_email_requests_are_limited_per_account_before_identity_lookup(
    client, mail, db
):
    """存在しないアドレスにも同じ2回/分の制限を適用し、識別子を平文保存しない。"""
    address = "budget-user@example.com"
    responses = [
        client.post("/auth/password-reset/request", json={"email": address})
        for _ in range(3)
    ]
    assert [response.status_code for response in responses] == [202, 202, 429]
    assert responses[-1].json()["code"] == "RATE_LIMITED"
    assert responses[-1].headers.get("Retry-After")
    assert not mail.messages
    assert all(address not in row.key for row in db.query(RequestLimit).all())


def test_email_requests_are_limited_per_ip_across_distinct_addresses(client, mail):
    """アドレスを替えても同じIPの申請を5回/分に制限する。"""
    responses = [
        client.post(
            "/auth/password-reset/request",
            json={"email": f"budget-{index}@example.com"},
        )
        for index in range(6)
    ]
    assert [response.status_code for response in responses] == [202] * 5 + [429]
    assert responses[-1].json()["code"] == "RATE_LIMITED"
    assert not mail.messages


def test_authenticated_account_updates_still_require_csrf(
    client, db, create_test_user, mail
):
    """Cookieを送る更新・確認APIではCSRFを省略できない。"""
    user = create_test_user(email="csrf-action@example.com")
    token = _request_reset(client, user, mail)
    authorize_as(client, user)
    del client.headers[settings.csrf_header_name]
    for path, payload in (
        ("/auth/email-change/request", {"new_email": "csrf-new@example.com"}),
        ("/auth/account-actions/inspect", {"token": token}),
        ("/auth/password-reset/confirm", {"token": token, "password": NEW_PASSWORD}),
    ):
        response = client.post(path, json=payload)
        assert response.status_code == 403
        assert response.json()["code"] == "CSRF_TOKEN_INVALID"
    db.expire_all()
    assert _action(db, token).consumed_at is None
    assert user.email == "csrf-action@example.com"


def test_concurrent_reset_confirmation_consumes_link_once(
    client, db, create_test_user, mail
):
    """同じリンクを並行使用しても1件だけ成功し、二度目の変更を許可しない。"""
    user = create_test_user(email="concurrent-reset@example.com")
    token = _request_reset(client, user, mail)
    passwords = ["Concurrent-first-2026!", "Concurrent-second-2026!"]
    with TestClient(client.app) as second_client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    target.post,
                    "/auth/password-reset/confirm",
                    json={"token": token, "password": password},
                )
                for target, password in zip((client, second_client), passwords)
            ]
            responses = [future.result(timeout=15) for future in futures]
    assert sorted(response.status_code for response in responses) == [200, 400]
    rejected = next(response for response in responses if response.status_code == 400)
    assert rejected.json()["code"] == "ACCOUNT_ACTION_INVALID"
    successful_password = passwords[
        next(
            index
            for index, response in enumerate(responses)
            if response.status_code == 200
        )
    ]
    db.expire_all()
    assert verify_password(successful_password, user.hashed_password)
    assert _action(db, token).consumed_at is not None
