"""運営発行・初回設定の強制・期限切れ・配信失敗の回帰検証。"""

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import EmailUnavailableError
from app.core.security import verify_password
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant, TenantMember
from app.models.user import User
from app.services.account_action import AccountActionService
from app.services.email import EmailService
from tests.helpers.auth import authorize_as


@pytest.fixture
def issued(client, db, create_test_user, monkeypatch):
    """テスト用メールだけへ配信し、作成者の運営セッションと発行結果を返す。"""
    messages = []
    monkeypatch.setattr(
        EmailService,
        "send",
        lambda self, message: messages.append(message) or "test-message",
    )
    monkeypatch.setattr(EmailService, "ensure_available", lambda self: None)
    admin = create_test_user(email="operator@example.com", system_role="system_admin")
    authorize_as(client, admin)
    body = {
        "name": "Customer A",
        "slug": "customer-a",
        "owner_email": "owner@example.com",
        "owner_name": "Customer Owner",
    }
    response = client.post("/tenants/issuance", json=body)
    assert response.status_code == 201
    assert response.json()["email_delivery"] == "sent"
    match = re.search(r"初回パスワード: (\S+)", messages[-1].text)
    assert match
    owner = db.query(User).filter(User.email == body["owner_email"]).one()
    return {
        "admin": admin,
        "owner": owner,
        "tenant": response.json()["tenant"],
        "password": match.group(1),
        "messages": messages,
        "body": body,
    }


def login_initial(client, issued):
    """初回パスワードでログインしてCSRFを設定する。"""
    response = client.post(
        "/auth/login",
        json={"email": issued["owner"].email, "password": issued["password"]},
    )
    assert response.status_code == 200
    assert response.json()["password_change_required"] is True
    client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]


def test_issue_creates_only_owner_and_keeps_credentials_out_of_response_and_audit(
    client, db, issued
):
    """権限を自動昇格させず、初回パスワードをメール以外に保存・公開しない。"""
    owner = issued["owner"]
    assert owner.password_change_required
    assert datetime.now(UTC) + timedelta(days=6) < owner.initial_password_expires_at
    assert verify_password(issued["password"], owner.hashed_password)
    assert db.query(TenantMember).filter(TenantMember.user_id == owner.id).count() == 1
    response = client.get(f"/users/{owner.id}")
    assert issued["password"] not in response.text
    assert response.json()["system_roles"] == []
    assert all(
        issued["password"] not in repr(event.extra_metadata)
        for event in db.query(AuditLog).all()
    )
    assert issued["password"] not in repr(issued["messages"])


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/tenants", None),
        ("GET", "/projects", None),
        ("GET", "/users", None),
        ("PATCH", "/auth/me", {"version": 1, "name": "Bypass"}),
        (
            "POST",
            "/tenants",
            {"name": "Bypass", "slug": "bypass", "owner_email": "owner@example.com"},
        ),
        ("POST", "/auth/email-change/request", {"new_email": "changed@example.com"}),
    ],
)
def test_initial_session_cannot_use_business_or_management(
    client, issued, method, path, body
):
    """組織Ownerであっても初回設定前のAPI直接操作を拒否する。"""
    with TestClient(client.app) as owner_client:
        login_initial(owner_client, issued)
        assert owner_client.get("/auth/me").json()["password_change_required"] is True
        response = owner_client.request(method, path, json=body)
        assert response.status_code == 403
        assert response.json()["code"] == "PASSWORD_CHANGE_REQUIRED"


def test_setup_revokes_all_initial_sessions_and_requires_new_login(client, db, issued):
    """設定前の複数ログインを失効し、初回パスワード・同じ要求の再使用を拒否する。"""
    with TestClient(client.app) as first, TestClient(client.app) as second:
        login_initial(first, issued)
        login_initial(second, issued)
        response = first.post(
            "/auth/initial-password",
            json={
                "current_password": issued["password"],
                "password": "my-personal-password123",
            },
        )
        assert response.status_code == 200
        assert first.get("/auth/me").status_code == 401
        assert second.get("/auth/me").status_code == 401
        assert (
            second.post(
                "/auth/initial-password",
                json={
                    "current_password": issued["password"],
                    "password": "my-personal-password123",
                },
            ).status_code
            == 401
        )
        assert (
            first.post(
                "/auth/login",
                json={"email": issued["owner"].email, "password": issued["password"]},
            ).status_code
            == 401
        )
        response = first.post(
            "/auth/login",
            json={
                "email": issued["owner"].email,
                "password": "my-personal-password123",
            },
        )
        assert response.status_code == 200
        assert response.json()["password_change_required"] is False
        assert first.get("/tenants").status_code == 200
    db.expire_all()
    assert db.get(User, issued["owner"].id).initial_password_expires_at is None


@pytest.mark.parametrize("password", ["too-short", "__initial__"])
def test_setup_rejects_weak_or_reused_initial_password(client, db, issued, password):
    """ポリシー違反でも初回設定状態を解除せず、既存セッションを維持する。"""
    with TestClient(client.app) as owner_client:
        login_initial(owner_client, issued)
        response = owner_client.post(
            "/auth/initial-password",
            json={
                "current_password": issued["password"],
                "password": issued["password"]
                if password == "__initial__"
                else password,
            },
        )
        assert response.status_code in (400, 422)
        assert owner_client.get("/auth/me").json()["password_change_required"]
    db.refresh(issued["owner"])
    assert verify_password(issued["password"], issued["owner"].hashed_password)


def test_expired_initial_password_requires_email_recovery(client, db, issued):
    """期限切れはログインも初回設定も拒否し、メール本人確認で復旧できる。"""
    with TestClient(client.app) as owner_client:
        login_initial(owner_client, issued)
        issued["owner"].initial_password_expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
        db.commit()
        response = owner_client.post(
            "/auth/initial-password",
            json={
                "current_password": issued["password"],
                "password": "my-personal-password123",
            },
        )
        assert response.json()["code"] == "INITIAL_PASSWORD_EXPIRED"
        response = owner_client.post(
            "/auth/login",
            json={"email": issued["owner"].email, "password": issued["password"]},
        )
        assert response.json()["code"] == "INITIAL_PASSWORD_EXPIRED"
    actions = AccountActionService()
    actions.request_password_reset(db, user_id=issued["owner"].id)
    match = re.search(r"#token=([A-Za-z0-9_-]+)", issued["messages"][-1].text)
    assert match is not None
    token = match.group(1)
    assert (
        client.post(
            "/auth/password-reset/confirm",
            json={"token": token, "password": "my-personal-password123"},
        ).status_code
        == 200
    )
    db.refresh(issued["owner"])
    assert not issued["owner"].password_change_required
    assert issued["owner"].initial_password_expires_at is None


def test_issue_preserves_existing_user_credentials(client, db, issued):
    """複数組織に所属するIdentityを再発行しても共有パスワードを変更しない。"""
    old_hash = issued["admin"].hashed_password
    body = {
        **issued["body"],
        "slug": "existing-owner",
        "owner_email": issued["admin"].email,
    }
    response = client.post("/tenants/issuance", json=body)
    assert response.status_code == 201
    db.refresh(issued["admin"])
    assert issued["admin"].hashed_password == old_hash
    assert not issued["admin"].password_change_required
    assert "初回パスワード:" not in issued["messages"][-1].text


def test_delivery_failure_preserves_created_tenant_and_resend_rotates_initial_password(
    client, db, issued, monkeypatch
):
    """SMTP失敗後も組織を二重作成せず、再送で初回パスワードと期限だけ再発行する。"""

    def fail(self, message):
        raise EmailUnavailableError()

    monkeypatch.setattr(EmailService, "send", fail)
    response = client.post(
        "/tenants/issuance",
        json={
            **issued["body"],
            "slug": "mail-failed",
            "owner_email": "failed@example.com",
        },
    )
    assert response.status_code == 201
    assert response.json()["email_delivery"] == "failed"
    tenant_id = response.json()["tenant"]["id"]
    assert db.get(Tenant, tenant_id)
    user = db.query(User).filter(User.email == "failed@example.com").one()
    old_hash = user.hashed_password
    monkeypatch.setattr(
        EmailService,
        "send",
        lambda self, message: issued["messages"].append(message) or "retry",
    )
    response = client.post(
        f"/tenants/{tenant_id}/welcome-email", json={"owner_email": user.email}
    )
    assert response.json()["email_delivery"] == "sent"
    db.refresh(user)
    assert user.hashed_password != old_hash
    assert "初回パスワード:" in issued["messages"][-1].text


def test_duplicate_tenant_does_not_leave_new_identity(client, db, issued):
    """組織の重複で失敗したとき、新規OwnerのIdentityも同時にロールバックする。"""
    response = client.post(
        "/tenants/issuance",
        json={**issued["body"], "owner_email": "rollback@example.com"},
    )
    assert response.status_code == 409
    assert db.query(User).filter(User.email == "rollback@example.com").first() is None


def test_resend_cannot_target_non_owner_and_does_not_reset_completed_owner(
    client, db, issued
):
    """他人宛の配送を拒否し、初回設定済みのOwnerには資格情報を再発行しない。"""
    tenant_id = issued["tenant"]["id"]
    assert (
        client.post(
            f"/tenants/{tenant_id}/welcome-email",
            json={"owner_email": issued["admin"].email},
        ).status_code
        == 404
    )
    issued["owner"].password_change_required = False
    issued["owner"].initial_password_expires_at = None
    old_hash = issued["owner"].hashed_password
    db.commit()
    response = client.post(
        f"/tenants/{tenant_id}/welcome-email",
        json={"owner_email": issued["owner"].email},
    )
    assert response.status_code == 200
    assert "初回パスワード:" not in issued["messages"][-1].text
    db.refresh(issued["owner"])
    assert old_hash == issued["owner"].hashed_password


def test_initial_session_never_becomes_a_business_session(client, db, issued):
    """本人の状態が設定済みに変わっても、発行済みの初回JWTは業務権限を取得しない。"""
    with TestClient(client.app) as owner_client:
        login_initial(owner_client, issued)
        issued["owner"].password_change_required = False
        db.commit()
        response = owner_client.get("/tenants")
        assert response.status_code == 403
        assert response.json()["code"] == "PASSWORD_CHANGE_REQUIRED"


def test_setup_requires_csrf_and_revalidates_current_password(client, issued):
    """ログインCookieだけの更新や、初回パスワードの誤入力を拒否する。"""
    with TestClient(client.app) as owner_client:
        login_initial(owner_client, issued)
        body = {
            "current_password": "incorrect-password",
            "password": "my-personal-password123",
        }
        del owner_client.headers["X-CSRF-Token"]
        assert (
            owner_client.post("/auth/initial-password", json=body).json()["code"]
            == "CSRF_TOKEN_INVALID"
        )
        owner_client.headers["X-CSRF-Token"] = owner_client.cookies["csrf_token"]
        assert (
            owner_client.post("/auth/initial-password", json=body).json()["code"]
            == "INVALID_CREDENTIALS"
        )


def test_non_operator_cannot_issue_or_send_welcome(
    client, db, issued, create_test_user
):
    """組織管理者でも運営承認の発行APIを利用できない。"""
    user = create_test_user(email="ordinary@example.com")
    authorize_as(client, user)
    assert client.post("/tenants/issuance", json=issued["body"]).status_code == 403
    assert (
        client.post(
            f"/tenants/{issued['tenant']['id']}/welcome-email",
            json={"owner_email": issued["owner"].email},
        ).status_code
        == 403
    )
