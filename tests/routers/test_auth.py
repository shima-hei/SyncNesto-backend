"""認証APIのテスト。"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token, verify_password
from app.models.user import User


class FakeStorageService:
    """テスト用StorageService。"""

    def __init__(self) -> None:
        """FakeStorageServiceを初期化する。"""
        self.deleted_keys: list[str] = []

    def upload_user_avatar(
        self,
        *,
        user_id: int,
        content: bytes,
        content_type: str | None,
    ) -> str:
        """固定のavatar keyを返す。"""
        return f"users/{user_id}.png"

    def generate_presigned_url(self, avatar_key: str | None) -> str | None:
        """固定の署名付きURLを返す。"""
        if avatar_key is None:
            return None

        return f"https://example.com/{avatar_key}?signature=test"

    def delete_object(self, key: str) -> None:
        """削除対象keyを保持する。"""
        self.deleted_keys.append(key)


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
    assert "access_token" not in response.json()
    assert response.json()["token_type"] == "bearer"
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
    access_token = create_access_token(subject=user.email)
    client.cookies.set(
        settings.auth_cookie_name,
        access_token,
        domain="testserver.local",
        path="/",
    )

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
    access_token = create_access_token(subject=user.email)

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
    access_token = create_access_token(subject=user.email)
    client.cookies.set(
        settings.auth_cookie_name,
        access_token,
        domain="testserver.local",
        path="/",
    )

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
    access_token = create_access_token(subject=user.email)
    client.cookies.set(
        settings.auth_cookie_name,
        access_token,
        domain="testserver.local",
        path="/",
    )

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
    access_token = create_access_token(subject=user.email)
    client.cookies.set(
        settings.auth_cookie_name,
        access_token,
        domain="testserver.local",
        path="/",
    )

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
    access_token = create_access_token(subject=user.email)
    client.cookies.set(
        settings.auth_cookie_name,
        access_token,
        domain="testserver.local",
        path="/",
    )

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
    access_token = create_access_token(subject=user.email)
    client.cookies.set(
        settings.auth_cookie_name,
        access_token,
        domain="testserver.local",
        path="/",
    )

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


def test_get_me_rejects_expired_token(client: TestClient) -> None:
    """期限切れtokenで現在ユーザー取得を拒否する。"""
    expired_token = jwt.encode(
        {
            "sub": "expired@example.com",
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
    access_token = create_access_token(subject=user.email)
    client.cookies.set(
        settings.auth_cookie_name,
        access_token,
        domain="testserver.local",
        path="/",
    )

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
    access_token = create_access_token(subject=user.email)
    client.cookies.set(
        settings.auth_cookie_name,
        access_token,
        domain="testserver.local",
        path="/",
    )

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
    access_token = create_access_token(subject=user.email)
    client.cookies.set(
        settings.auth_cookie_name,
        access_token,
        domain="testserver.local",
        path="/",
    )

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


def test_logout_deletes_auth_cookie(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """ログアウトAPIが認証Cookieを削除することを確認する。"""
    user = create_test_user(email="logout@example.com")
    access_token = create_access_token(subject=user.email)
    client.cookies.set(
        settings.auth_cookie_name,
        access_token,
        domain="testserver.local",
        path="/",
    )

    response = client.post("/auth/logout")

    assert response.status_code == 204
    assert response.content == b""
    assert client.cookies.get(settings.auth_cookie_name) is None
    assert settings.auth_cookie_name in response.headers["set-cookie"]
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert "httponly" in response.headers["set-cookie"].lower()


def test_get_me_rejects_after_logout(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """ログアウト後に現在ユーザー取得を拒否する。"""
    user = create_test_user(email="logout-me@example.com")
    access_token = create_access_token(subject=user.email)
    client.cookies.set(
        settings.auth_cookie_name,
        access_token,
        domain="testserver.local",
        path="/",
    )

    logout_response = client.post("/auth/logout")
    response = client.get("/auth/me")

    assert logout_response.status_code == 204
    assert response.status_code == 401
    assert response.json() == {
        "message": "Authentication required",
        "code": "AUTHENTICATION_REQUIRED",
    }
