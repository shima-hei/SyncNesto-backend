"""認証APIのテスト。"""

from collections.abc import Callable

from fastapi.testclient import TestClient
import pytest

from app.core.config import settings
from app.core.security import create_access_token
from app.models.user import User


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
        "system_roles": [
            {
                "key": "system_admin",
                "name": "システム管理者",
            }
        ],
    }


def test_get_me_rejects_missing_token(client: TestClient) -> None:
    """tokenなしの現在ユーザー取得を拒否する。"""
    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json() == {
        "message": "Invalid email or password",
        "code": "INVALID_CREDENTIALS",
    }


def test_get_me_rejects_invalid_token(client: TestClient) -> None:
    """不正tokenで現在ユーザー取得を拒否する。"""
    response = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "message": "Invalid email or password",
        "code": "INVALID_CREDENTIALS",
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
        "message": "Invalid email or password",
        "code": "INVALID_CREDENTIALS",
    }
