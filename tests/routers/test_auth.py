"""認証APIのテスト。"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.csrf import generate_csrf_token
from app.core.security import create_access_token, decode_access_token, verify_password
from app.models.audit_log import AuditLog
from app.models.user import User
from app.repositories.login_attempt import LoginAttemptRepository
from app.repositories.session import UserSessionRepository
from tests.fakes.storage import FakeStorageService
from tests.helpers.auth import authorize_as, create_session_token


@pytest.fixture(autouse=True)
def use_fake_storage_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """認証APIテストではStorageServiceをfakeに差し替える。"""
    from app.routers import auth

    monkeypatch.setattr(auth, "storage_service", FakeStorageService())

def test_login_user_returns_access_token_and_cookie_in_development(
    client: TestClient,
    create_test_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """開発環境のログインレスポンスを確認する。"""
    monkeypatch.setattr(settings, "allow_bearer_token_response", True)
    monkeypatch.setattr(settings, "auth_cookie_secure", False)

    password = "password123"
    create_test_user(
        email="login@example.com",
        name="Login User",
        password=password,
    )

    response = client.post(
        "/auth/login",
        json={
            "email": "login@example.com",
            "password": password,
        },
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Login successful"
    assert isinstance(response.json()["access_token"], str)
    assert response.json()["token_type"] == "bearer"
    assert response.cookies.get(settings.auth_cookie_name)
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "password" not in response.text
    assert "hashed_password" not in response.text


def test_login_user_does_not_return_access_token_in_production(
    client: TestClient,
    create_test_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本番相当設定のログインレスポンスを確認する。"""
    monkeypatch.setattr(settings, "allow_bearer_token_response", False)
    monkeypatch.setattr(settings, "auth_cookie_secure", True)

    password = "password123"
    create_test_user(
        email="cookie-only@example.com",
        name="Cookie Only User",
        password=password,
    )

    response = client.post(
        "/auth/login",
        json={
            "email": "cookie-only@example.com",
            "password": password,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Login successful"}
    assert response.cookies.get(settings.auth_cookie_name)
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "secure" in response.headers["set-cookie"].lower()


def test_login_user_updates_last_login_at_without_incrementing_version(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """ログイン成功時にlast_login_atを更新し、versionを増やさないことを確認する。"""
    password = "password123"
    user = create_test_user(
        email="last-login@example.com",
        name="Last Login User",
        password=password,
    )

    response = client.post(
        "/auth/login",
        json={
            "email": "last-login@example.com",
            "password": password,
        },
    )

    assert response.status_code == 200
    db.refresh(user)
    assert user.last_login_at is not None
    assert user.version == 1


def test_login_user_creates_session_and_returns_sid_token(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """ログイン成功時にDBセッションを作成し、sid入りtokenを返すことを確認する。"""
    password = "password123"
    user = create_test_user(
        email="session-login@example.com",
        name="Session Login User",
        password=password,
    )

    response = client.post(
        "/auth/login",
        json={
            "email": "session-login@example.com",
            "password": password,
        },
    )

    assert response.status_code == 200
    payload = decode_access_token(response.cookies[settings.auth_cookie_name])
    assert payload["sub"] == "session-login@example.com"
    assert isinstance(payload["sid"], str)

    user_session = UserSessionRepository().get_by_id(db, UUID(payload["sid"]))
    assert user_session is not None
    assert user_session.user_id == user.id
    assert user_session.revoked_at is None
    assert user_session.revoked_reason is None


def test_login_user_records_success_audit_log(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """ログイン成功時に監査ログを記録することを確認する。"""
    password = "password123"
    user = create_test_user(
        email="audit-login@example.com",
        name="Audit Login User",
        password=password,
    )

    response = client.post(
        "/auth/login",
        headers={"X-Request-ID": "audit-login-request"},
        json={
            "email": "audit-login@example.com",
            "password": password,
        },
    )

    audit_log = db.query(AuditLog).filter_by(event_type="auth.login.success").one()
    assert response.status_code == 200
    assert audit_log.actor_user_id == user.id
    assert audit_log.target_user_id == user.id
    assert audit_log.resource_type == "session"
    assert audit_log.request_id == "audit-login-request"
    assert audit_log.user_agent is not None
    assert audit_log.extra_metadata["email"] == user.email
    assert "password" not in audit_log.extra_metadata


def test_login_user_sets_csrf_cookie(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """ログイン成功時にCSRF Cookieを設定することを確認する。"""
    password = "password123"
    create_test_user(
        email="csrf-login@example.com",
        name="CSRF Login User",
        password=password,
    )

    response = client.post(
        "/auth/login",
        json={
            "email": "csrf-login@example.com",
            "password": password,
        },
    )

    csrf_set_cookie = next(
        cookie
        for cookie in response.headers.get_list("set-cookie")
        if cookie.startswith(f"{settings.csrf_cookie_name}=")
    )
    assert response.status_code == 200
    assert response.cookies.get(settings.csrf_cookie_name)
    assert "httponly" not in csrf_set_cookie.lower()


def test_login_user_rejects_inactive_user(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """無効ユーザーのログインを拒否することを確認する。"""
    password = "password123"
    user = create_test_user(
        email="inactive@example.com",
        name="Inactive User",
        password=password,
    )
    user.is_active = False
    db.commit()

    response = client.post(
        "/auth/login",
        json={
            "email": "inactive@example.com",
            "password": password,
        },
    )

    assert response.status_code == 401
    assert response.json() == {
        "message": "Invalid email or password",
        "code": "INVALID_CREDENTIALS",
    }


def test_login_user_rejects_unknown_email(client: TestClient) -> None:
    """ログインAPIが存在しないemailを拒否することを確認する。"""
    response = client.post(
        "/auth/login",
        json={
            "email": "unknown@example.com",
            "password": "password123",
        },
    )

    assert response.status_code == 401
    assert response.json() == {
        "message": "Invalid email or password",
        "code": "INVALID_CREDENTIALS",
    }


def test_login_user_records_failure_audit_log(
    client: TestClient,
    db: Session,
) -> None:
    """ログイン失敗時に監査ログを記録することを確認する。"""
    response = client.post(
        "/auth/login",
        json={
            "email": "audit-failure@example.com",
            "password": "password123",
        },
    )

    audit_log = db.query(AuditLog).filter_by(event_type="auth.login.failure").one()
    assert response.status_code == 401
    assert audit_log.actor_user_id is None
    assert audit_log.target_user_id is None
    assert audit_log.extra_metadata == {
        "email": "audit-failure@example.com",
        "reason": "unknown_email",
    }


def test_login_user_rejects_wrong_password(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """ログインAPIが誤ったpasswordを拒否することを確認する。"""
    create_test_user(
        email="wrong-password@example.com",
        name="Wrong Password User",
        password="password123",
    )

    response = client.post(
        "/auth/login",
        json={
            "email": "wrong-password@example.com",
            "password": "wrong-password",
        },
    )

    assert response.status_code == 401
    assert response.json() == {
        "message": "Invalid email or password",
        "code": "INVALID_CREDENTIALS",
    }


def test_login_user_records_failed_attempt_for_wrong_password(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """誤ったpasswordでログイン失敗回数を記録することを確認する。"""
    create_test_user(
        email="attempt-wrong@example.com",
        name="Attempt Wrong User",
        password="password123",
    )

    response = client.post(
        "/auth/login",
        json={
            "email": "attempt-wrong@example.com",
            "password": "wrong-password",
        },
    )

    login_attempt = LoginAttemptRepository().get_by_email(
        db,
        "attempt-wrong@example.com",
    )
    assert response.status_code == 401
    assert login_attempt is not None
    assert login_attempt.failed_count == 1
    assert login_attempt.locked_until is None
    assert login_attempt.last_failed_at is not None


def test_login_user_records_failed_attempt_for_unknown_email(
    client: TestClient,
    db: Session,
) -> None:
    """存在しないemailでもログイン失敗回数を記録することを確認する。"""
    response = client.post(
        "/auth/login",
        json={
            "email": "UNKNOWN@example.com",
            "password": "password123",
        },
    )

    login_attempt = LoginAttemptRepository().get_by_email(db, "unknown@example.com")
    assert response.status_code == 401
    assert response.json() == {
        "message": "Invalid email or password",
        "code": "INVALID_CREDENTIALS",
    }
    assert login_attempt is not None
    assert login_attempt.failed_count == 1


def test_login_user_records_failed_attempt_for_inactive_user(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """無効ユーザーでもログイン失敗回数を記録することを確認する。"""
    user = create_test_user(
        email="attempt-inactive@example.com",
        name="Attempt Inactive User",
        password="password123",
    )
    user.is_active = False
    db.commit()

    response = client.post(
        "/auth/login",
        json={
            "email": "attempt-inactive@example.com",
            "password": "password123",
        },
    )

    login_attempt = LoginAttemptRepository().get_by_email(
        db,
        "attempt-inactive@example.com",
    )
    assert response.status_code == 401
    assert login_attempt is not None
    assert login_attempt.failed_count == 1


def test_login_user_locks_email_after_failed_attempt_limit(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失敗回数が閾値に達したemailを一時ロックすることを確認する。"""
    monkeypatch.setattr(settings, "login_max_failed_attempts", 2)
    monkeypatch.setattr(settings, "login_lock_minutes", 15)
    create_test_user(
        email="locked@example.com",
        name="Locked User",
        password="password123",
    )

    for _ in range(2):
        response = client.post(
            "/auth/login",
            json={
                "email": "locked@example.com",
                "password": "wrong-password",
            },
        )
        assert response.status_code == 401

    login_attempt = LoginAttemptRepository().get_by_email(db, "locked@example.com")
    assert login_attempt is not None
    assert login_attempt.failed_count == 2
    assert login_attempt.locked_until is not None
    assert login_attempt.locked_until > datetime.now(UTC)


def test_login_user_rejects_correct_password_while_locked(
    client: TestClient,
    create_test_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ロック中は正しいpasswordでもログインを拒否することを確認する。"""
    monkeypatch.setattr(settings, "login_max_failed_attempts", 1)
    create_test_user(
        email="locked-correct@example.com",
        name="Locked Correct User",
        password="password123",
    )

    failed_response = client.post(
        "/auth/login",
        json={
            "email": "locked-correct@example.com",
            "password": "wrong-password",
        },
    )
    success_attempt_response = client.post(
        "/auth/login",
        json={
            "email": "locked-correct@example.com",
            "password": "password123",
        },
    )

    assert failed_response.status_code == 401
    assert success_attempt_response.status_code == 401
    assert success_attempt_response.json() == {
        "message": "Invalid email or password",
        "code": "INVALID_CREDENTIALS",
    }


def test_login_user_allows_login_after_lock_expired(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ロック期限切れ後は正しいpasswordでログインできることを確認する。"""
    monkeypatch.setattr(settings, "login_max_failed_attempts", 1)
    create_test_user(
        email="expired-lock@example.com",
        name="Expired Lock User",
        password="password123",
    )
    client.post(
        "/auth/login",
        json={
            "email": "expired-lock@example.com",
            "password": "wrong-password",
        },
    )
    login_attempt = LoginAttemptRepository().get_by_email(
        db,
        "expired-lock@example.com",
    )
    assert login_attempt is not None
    login_attempt.locked_until = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()

    response = client.post(
        "/auth/login",
        json={
            "email": "expired-lock@example.com",
            "password": "password123",
        },
    )

    db.refresh(login_attempt)
    assert response.status_code == 200
    assert login_attempt.failed_count == 0
    assert login_attempt.locked_until is None


def test_login_user_resets_failed_attempts_on_success(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """ログイン成功時に失敗回数をリセットすることを確認する。"""
    create_test_user(
        email="reset-attempt@example.com",
        name="Reset Attempt User",
        password="password123",
    )
    client.post(
        "/auth/login",
        json={
            "email": "reset-attempt@example.com",
            "password": "wrong-password",
        },
    )

    response = client.post(
        "/auth/login",
        json={
            "email": "reset-attempt@example.com",
            "password": "password123",
        },
    )

    login_attempt = LoginAttemptRepository().get_by_email(
        db,
        "reset-attempt@example.com",
    )
    assert response.status_code == 200
    assert login_attempt is not None
    assert login_attempt.failed_count == 0
    assert login_attempt.locked_until is None
    assert login_attempt.last_failed_at is None


def test_login_user_requires_password(client: TestClient) -> None:
    """ログインAPIがpassword未指定時に422を返すことを確認する。"""
    response = client.post(
        "/auth/login",
        json={
            "email": "missing-login-password@example.com",
        },
    )

    assert response.status_code == 422


def test_get_me_returns_current_user_with_cookie_token(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """Cookie tokenで現在のユーザーを取得できることを確認する。"""
    user = create_test_user(
        email="me-cookie@example.com",
        name="Me Cookie User",
        password="password123",
    )
    authorize_as(client, user)

    response = client.get("/auth/me")

    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "email": "me-cookie@example.com",
        "name": "Me Cookie User",
        "version": 1,
        "department": None,
        "position": None,
        "avatar_url": "https://example.com/default-avatar.png?signature=test",
        "is_active": True,
        "last_login_at": None,
        "created_by": None,
        "updated_by": None,
        "system_roles": [],
    }


def test_get_me_returns_current_user_with_authorization_header(
    client: TestClient,
    create_test_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bearer tokenで現在のユーザーを取得できることを確認する。"""
    monkeypatch.setattr(settings, "allow_authorization_header", True)
    user = create_test_user(
        email="me-bearer@example.com",
        name="Me Bearer User",
        password="password123",
    )
    access_token, _ = create_session_token(user)

    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "email": "me-bearer@example.com",
        "name": "Me Bearer User",
        "version": 1,
        "department": None,
        "position": None,
        "avatar_url": "https://example.com/default-avatar.png?signature=test",
        "is_active": True,
        "last_login_at": None,
        "created_by": None,
        "updated_by": None,
        "system_roles": [],
    }


def test_get_me_returns_system_roles(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """現在ユーザー取得でsystem roleを返すことを確認する。"""
    user = create_test_user(
        email="admin@example.com",
        name="Admin",
        system_role="system_admin",
    )
    authorize_as(client, user)

    response = client.get("/auth/me")

    assert response.status_code == 200
    assert response.json() == {
        "id": user.id,
        "email": "admin@example.com",
        "name": "Admin",
        "version": 1,
        "department": None,
        "position": None,
        "avatar_url": "https://example.com/default-avatar.png?signature=test",
        "is_active": True,
        "last_login_at": None,
        "created_by": None,
        "updated_by": None,
        "system_roles": [
            {
                "key": "system_admin",
                "name": "システム管理者",
            }
        ],
    }


def test_update_me_updates_current_user_profile(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """本人プロフィールを更新できることを確認する。"""
    user = create_test_user(
        email="profile@example.com",
        name="Before",
        password="password123",
    )
    authorize_as(client, user)

    response = client.patch(
        "/auth/me",
        json={
            "name": "After",
            "password": "new-password123",
            "version": user.version,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": user.id,
        "email": "profile@example.com",
        "name": "After",
        "version": user.version + 1,
        "department": None,
        "position": None,
        "avatar_url": "https://example.com/default-avatar.png?signature=test",
        "is_active": True,
        "last_login_at": None,
        "created_by": None,
        "updated_by": user.id,
        "system_roles": [],
    }
    db.refresh(user)
    assert user.name == "After"
    assert user.updated_by == user.id
    assert verify_password("new-password123", user.hashed_password)


def test_update_me_rejects_email_update(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """本人プロフィール更新でemail変更を拒否することを確認する。"""
    user = create_test_user(email="profile@example.com")
    authorize_as(client, user)

    response = client.patch(
        "/auth/me",
        json={
            "email": "changed@example.com",
            "version": user.version,
        },
    )

    assert response.status_code == 422


def test_update_me_rejects_admin_only_profile_fields(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """本人プロフィール更新で管理者用項目の変更を拒否することを確認する。"""
    user = create_test_user(email="profile@example.com")
    authorize_as(client, user)

    response = client.patch(
        "/auth/me",
        json={
            "department": "QA",
            "position": "Lead",
            "is_active": False,
            "version": user.version,
        },
    )

    assert response.status_code == 422


def test_update_me_avatar_uploads_image_and_returns_presigned_url(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本人アイコン画像をアップロードして署名付きURLを返すことを確認する。"""
    from app.routers import auth

    monkeypatch.setattr(auth, "storage_service", FakeStorageService())
    user = create_test_user(email="avatar@example.com")
    authorize_as(client, user)

    response = client.put(
        "/auth/me/avatar",
        files={"file": ("avatar.png", b"image-bytes", "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["avatar_url"] == (
        "https://example.com/users/1.png?signature=test"
    )
    assert response.json()["version"] == user.version + 1
    db.refresh(user)
    assert user.avatar_key == "users/1.png"
    assert user.updated_by == user.id


def test_update_me_avatar_requires_login(client: TestClient) -> None:
    """本人アイコン更新が未ログインユーザーを拒否することを確認する。"""
    response = client.put(
        "/auth/me/avatar",
        files={"file": ("avatar.png", b"image-bytes", "image/png")},
    )

    assert response.status_code == 401
    assert response.json() == {
        "message": "Authentication required",
        "code": "AUTHENTICATION_REQUIRED",
    }


def test_get_me_rejects_expired_token(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """期限切れtokenで現在ユーザー取得を拒否する。"""
    user = create_test_user(email="expired@example.com")
    _, session_id = create_session_token(user)
    expired_token = jwt.encode(
        {
            "sub": "expired@example.com",
            "sid": str(session_id),
            "exp": datetime.now(UTC) - timedelta(minutes=1),
        },
        settings.secret_key,
        algorithm=settings.algorithm,
    )

    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "message": "Token expired",
        "code": "TOKEN_EXPIRED",
    }
    user_session = UserSessionRepository().get_by_id(db, session_id)
    assert user_session is not None
    assert user_session.revoked_reason == "expired"


def test_get_me_rejects_expired_session_and_revokes_it(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """DBセッションのidle期限切れを拒否し、expiredとして失効する。"""
    user = create_test_user(email="expired-session@example.com")
    session_id = authorize_as(client, user)
    user_session = UserSessionRepository().get_by_id(db, session_id)
    assert user_session is not None
    user_session.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()

    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json() == {
        "message": "Token expired",
        "code": "TOKEN_EXPIRED",
    }
    assert settings.auth_cookie_name in response.headers["set-cookie"]
    assert "max-age=0" in response.headers["set-cookie"].lower()
    db.refresh(user_session)
    assert user_session.revoked_reason == "expired"


def test_get_me_rejects_absolute_expired_session_and_revokes_it(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """DBセッションの絶対期限切れを拒否し、absolute_expiredとして失効する。"""
    user = create_test_user(email="absolute-expired@example.com")
    session_id = authorize_as(client, user)
    user_session = UserSessionRepository().get_by_id(db, session_id)
    assert user_session is not None
    user_session.absolute_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()

    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json() == {
        "message": "Token expired",
        "code": "TOKEN_EXPIRED",
    }
    assert settings.auth_cookie_name in response.headers["set-cookie"]
    assert "max-age=0" in response.headers["set-cookie"].lower()
    db.refresh(user_session)
    assert user_session.revoked_reason == "absolute_expired"


def test_get_me_refreshes_cookie_when_session_is_near_expiration(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """セッション期限が閾値以下の場合にCookieを再発行する。"""
    user = create_test_user(email="refresh@example.com")
    session_id = authorize_as(client, user)
    user_session = UserSessionRepository().get_by_id(db, session_id)
    assert user_session is not None
    previous_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    user_session.expires_at = previous_expires_at
    db.commit()
    access_token = create_access_token(
        subject=user.email,
        session_id=session_id,
        expires_at=previous_expires_at,
    )
    client.cookies.set(
        settings.auth_cookie_name,
        access_token,
        domain="testserver.local",
        path="/",
    )

    response = client.get("/auth/me")

    assert response.status_code == 200
    assert settings.auth_cookie_name in response.headers["set-cookie"]
    db.refresh(user_session)
    assert user_session.expires_at > previous_expires_at


def test_get_me_does_not_refresh_cookie_when_session_has_enough_time(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """セッション期限に余裕がある場合はCookieを再発行しない。"""
    user = create_test_user(email="no-refresh@example.com")
    authorize_as(client, user)

    response = client.get("/auth/me")

    assert response.status_code == 200
    assert "set-cookie" not in response.headers


def test_get_me_rejects_revoked_session(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """失効済みセッションを拒否する。"""
    user = create_test_user(email="revoked@example.com")
    session_id = authorize_as(client, user)
    user_session = UserSessionRepository().get_by_id(db, session_id)
    assert user_session is not None
    UserSessionRepository().revoke(db, user_session, "admin_revoked")

    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json() == {
        "message": "Invalid token",
        "code": "INVALID_TOKEN",
    }
    assert settings.auth_cookie_name in response.headers["set-cookie"]
    assert "max-age=0" in response.headers["set-cookie"].lower()


def test_get_me_deletes_cookie_for_invalid_cookie_token(client: TestClient) -> None:
    """Cookieに不正tokenがある場合は削除Cookieを返す。"""
    client.cookies.set(
        settings.auth_cookie_name,
        "invalid-token",
        domain="testserver.local",
        path="/",
    )

    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json() == {
        "message": "Invalid token",
        "code": "INVALID_TOKEN",
    }
    assert settings.auth_cookie_name in response.headers["set-cookie"]
    assert "max-age=0" in response.headers["set-cookie"].lower()


def test_delete_me_avatar_resets_avatar_key_and_deletes_s3_object(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本人アイコン削除でDBをデフォルトに戻し、旧S3オブジェクトを削除する。"""
    from app.routers import auth

    fake_storage_service = FakeStorageService()
    monkeypatch.setattr(auth, "storage_service", fake_storage_service)
    user = create_test_user(email="delete-avatar@example.com")
    user.avatar_key = "users/1.png"
    db.commit()
    db.refresh(user)
    authorize_as(client, user)

    response = client.delete("/auth/me/avatar")

    assert response.status_code == 200
    assert response.json()["avatar_url"] == (
        "https://example.com/default-avatar.png?signature=test"
    )
    assert response.json()["version"] == user.version + 1
    db.refresh(user)
    assert user.avatar_key == settings.default_avatar_key
    assert user.updated_by == user.id
    assert fake_storage_service.deleted_keys == ["users/1.png"]


def test_delete_me_avatar_does_not_delete_default_avatar(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """デフォルト画像の本人アイコン削除ではS3削除もDB更新もしない。"""
    from app.routers import auth

    fake_storage_service = FakeStorageService()
    monkeypatch.setattr(auth, "storage_service", fake_storage_service)
    user = create_test_user(email="default-avatar@example.com")
    user.avatar_key = settings.default_avatar_key
    db.commit()
    db.refresh(user)
    current_version = user.version
    authorize_as(client, user)

    response = client.delete("/auth/me/avatar")

    assert response.status_code == 200
    assert response.json()["avatar_url"] == (
        "https://example.com/default-avatar.png?signature=test"
    )
    db.refresh(user)
    assert user.version == current_version
    assert fake_storage_service.deleted_keys == []


def test_delete_me_avatar_requires_login(client: TestClient) -> None:
    """本人アイコン削除が未ログインユーザーを拒否することを確認する。"""
    response = client.delete("/auth/me/avatar")

    assert response.status_code == 401
    assert response.json() == {
        "message": "Authentication required",
        "code": "AUTHENTICATION_REQUIRED",
    }


def test_update_me_rejects_stale_version_with_current_user(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """古いversionでの本人プロフィール更新を409で拒否する。"""
    user = create_test_user(email="profile@example.com", name="Before")
    user.name = "Latest"
    user.version = 2
    db.commit()
    db.refresh(user)
    authorize_as(client, user)

    response = client.patch(
        "/auth/me",
        json={"name": "Stale", "version": 1},
    )

    assert response.status_code == 409
    assert response.json() == {
        "message": "Resource version conflict",
        "code": "VERSION_CONFLICT",
        "current": {
            "id": user.id,
            "email": "profile@example.com",
            "name": "Latest",
            "version": 2,
            "department": None,
            "position": None,
            "avatar_url": None,
            "is_active": True,
            "last_login_at": None,
            "created_by": None,
            "updated_by": None,
            "system_roles": [],
        },
    }


def test_update_me_requires_login(client: TestClient) -> None:
    """本人プロフィール更新が未ログインユーザーを拒否することを確認する。"""
    response = client.patch("/auth/me", json={"name": "After", "version": 1})

    assert response.status_code == 401
    assert response.json() == {
        "message": "Authentication required",
        "code": "AUTHENTICATION_REQUIRED",
    }


def test_get_me_rejects_missing_token(client: TestClient) -> None:
    """tokenなしの現在ユーザー取得を拒否する。"""
    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json() == {
        "message": "Authentication required",
        "code": "AUTHENTICATION_REQUIRED",
    }
    assert "set-cookie" not in response.headers


def test_get_me_rejects_invalid_token(client: TestClient) -> None:
    """不正tokenで現在ユーザー取得を拒否する。"""
    response = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "message": "Invalid token",
        "code": "INVALID_TOKEN",
    }


def test_get_me_does_not_require_csrf_token(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """GETリクエストではCSRF tokenが不要なことを確認する。"""
    user = create_test_user(email="get-no-csrf@example.com")
    authorize_as(client, user)
    client.headers.pop(settings.csrf_header_name, None)

    response = client.get("/auth/me")

    assert response.status_code == 200


def test_update_me_rejects_missing_csrf_header(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """認証Cookieありの更新系リクエストでCSRF header未指定を拒否する。"""
    user = create_test_user(email="missing-csrf@example.com")
    authorize_as(client, user)
    client.headers.pop(settings.csrf_header_name, None)

    response = client.patch(
        "/auth/me",
        json={"name": "Missing CSRF", "version": 1},
    )

    assert response.status_code == 403
    assert response.json() == {
        "message": "Invalid CSRF token",
        "code": "CSRF_TOKEN_INVALID",
    }


def test_update_me_rejects_invalid_csrf_header(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """認証CookieとCSRF headerが一致しない場合に更新を拒否する。"""
    user = create_test_user(email="invalid-csrf@example.com")
    authorize_as(client, user)
    client.headers[settings.csrf_header_name] = "invalid-csrf-token"

    response = client.patch(
        "/auth/me",
        json={"name": "Invalid CSRF", "version": 1},
    )

    assert response.status_code == 403
    assert response.json() == {
        "message": "Invalid CSRF token",
        "code": "CSRF_TOKEN_INVALID",
    }


def test_update_me_accepts_valid_csrf_header(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """認証CookieとCSRF headerが一致する場合に更新を許可する。"""
    user = create_test_user(email="valid-csrf@example.com")
    authorize_as(client, user)

    response = client.patch(
        "/auth/me",
        json={"name": "Valid CSRF", "version": 1},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Valid CSRF"


def test_update_me_without_auth_cookie_does_not_run_csrf_check(
    client: TestClient,
) -> None:
    """認証Cookieなしの場合はCSRFではなく認証エラーを返すことを確認する。"""
    response = client.patch(
        "/auth/me",
        json={"name": "Anonymous", "version": 1},
    )

    assert response.status_code == 401
    assert response.json() == {
        "message": "Authentication required",
        "code": "AUTHENTICATION_REQUIRED",
    }


def test_logout_deletes_auth_cookie(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """ログアウトAPIが認証Cookieを削除することを確認する。"""
    user = create_test_user(email="logout@example.com")
    session_id = authorize_as(client, user)

    response = client.post("/auth/logout")

    assert response.status_code == 204
    assert response.content == b""
    assert client.cookies.get(settings.auth_cookie_name) is None
    assert client.cookies.get(settings.csrf_cookie_name) is None
    assert settings.auth_cookie_name in response.headers["set-cookie"]
    assert settings.csrf_cookie_name in response.headers["set-cookie"]
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert "httponly" in response.headers["set-cookie"].lower()
    user_session = UserSessionRepository().get_by_id(db, session_id)
    assert user_session is not None
    assert user_session.revoked_reason == "logout"


def test_logout_records_audit_log(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """ログアウト時に監査ログを記録することを確認する。"""
    user = create_test_user(email="logout-audit@example.com")
    session_id = authorize_as(client, user)

    response = client.post("/auth/logout")

    audit_log = db.query(AuditLog).filter_by(event_type="auth.logout").one()
    assert response.status_code == 204
    assert audit_log.actor_user_id == user.id
    assert audit_log.target_user_id == user.id
    assert audit_log.resource_type == "session"
    assert audit_log.extra_metadata["session_id"] == str(session_id)


def test_logout_rejects_missing_csrf_header(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """認証CookieありのログアウトでCSRF header未指定を拒否する。"""
    user = create_test_user(email="logout-missing-csrf@example.com")
    authorize_as(client, user)
    client.headers.pop(settings.csrf_header_name, None)

    response = client.post("/auth/logout")

    assert response.status_code == 403
    assert response.json() == {
        "message": "Invalid CSRF token",
        "code": "CSRF_TOKEN_INVALID",
    }


def test_logout_rejects_invalid_csrf_header(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """認証CookieとCSRF headerが一致しないログアウトを拒否する。"""
    user = create_test_user(email="logout-invalid-csrf@example.com")
    authorize_as(client, user)
    client.headers[settings.csrf_header_name] = generate_csrf_token()

    response = client.post("/auth/logout")

    assert response.status_code == 403
    assert response.json() == {
        "message": "Invalid CSRF token",
        "code": "CSRF_TOKEN_INVALID",
    }


def test_get_me_rejects_after_logout(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """ログアウト後に現在ユーザー取得を拒否する。"""
    user = create_test_user(email="logout-me@example.com")
    authorize_as(client, user)

    logout_response = client.post("/auth/logout")
    response = client.get("/auth/me")

    assert logout_response.status_code == 204
    assert response.status_code == 401
    assert response.json() == {
        "message": "Authentication required",
        "code": "AUTHENTICATION_REQUIRED",
    }
