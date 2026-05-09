"""認証APIのテスト。"""

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import verify_password
from app.models.user import User


def test_register_user_returns_created_user(client: TestClient) -> None:
    """ユーザー登録APIが作成済みユーザー情報を返すことを確認する。"""
    response = client.post(
        "/auth/register",
        json={
            "email": "user@example.com",
            "name": "User Name",
            "password": "password123",
        },
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": 1,
        "email": "user@example.com",
        "name": "User Name",
    }
    assert "password" not in response.json()
    assert "hashed_password" not in response.json()


def test_register_user_stores_hashed_password(
    client: TestClient,
    db: Session,
) -> None:
    """ユーザー登録APIがハッシュ済みパスワードを保存することを確認する。"""
    plain_password = "password123"

    response = client.post(
        "/auth/register",
        json={
            "email": "hash@example.com",
            "name": "Hash User",
            "password": plain_password,
        },
    )

    assert response.status_code == 201

    user = db.query(User).filter(User.email == "hash@example.com").one()
    assert user.hashed_password != plain_password
    assert verify_password(plain_password, user.hashed_password)


def test_register_user_rejects_duplicate_email(client: TestClient) -> None:
    """ユーザー登録APIが重複emailを拒否することを確認する。"""
    request_body = {
        "email": "duplicate@example.com",
        "name": "Duplicate User",
        "password": "password123",
    }

    first_response = client.post("/auth/register", json=request_body)
    second_response = client.post("/auth/register", json=request_body)

    assert first_response.status_code == 201
    assert second_response.status_code == 400
    assert second_response.json() == {
        "message": "Email already registered",
        "code": "EMAIL_ALREADY_REGISTERED",
    }


def test_register_user_requires_password(client: TestClient) -> None:
    """ユーザー登録APIがpassword未指定時に422を返すことを確認する。"""
    response = client.post(
        "/auth/register",
        json={
            "email": "missing-password@example.com",
            "name": "Missing Password User",
        },
    )

    assert response.status_code == 422


def test_login_user_returns_access_token_and_cookie_in_development(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """開発環境ではログインAPIがbodyとCookieにアクセストークンを返すことを確認する。"""
    monkeypatch.setattr(settings, "allow_bearer_token_response", True)
    monkeypatch.setattr(settings, "auth_cookie_secure", False)

    password = "password123"
    register_response = client.post(
        "/auth/register",
        json={
            "email": "login@example.com",
            "name": "Login User",
            "password": password,
        },
    )

    response = client.post(
        "/auth/login",
        json={
            "email": "login@example.com",
            "password": password,
        },
    )

    assert register_response.status_code == 201
    assert response.status_code == 200
    assert isinstance(response.json()["access_token"], str)
    assert response.json()["token_type"] == "bearer"
    assert response.cookies.get(settings.auth_cookie_name)
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "password" not in response.text
    assert "hashed_password" not in response.text


def test_login_user_does_not_return_access_token_in_production(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本番相当設定ではログインAPIがbodyにアクセストークンを返さないことを確認する。"""
    monkeypatch.setattr(settings, "allow_bearer_token_response", False)
    monkeypatch.setattr(settings, "auth_cookie_secure", True)

    password = "password123"
    register_response = client.post(
        "/auth/register",
        json={
            "email": "cookie-only@example.com",
            "name": "Cookie Only User",
            "password": password,
        },
    )

    response = client.post(
        "/auth/login",
        json={
            "email": "cookie-only@example.com",
            "password": password,
        },
    )

    assert register_response.status_code == 201
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


def test_login_user_rejects_wrong_password(client: TestClient) -> None:
    """ログインAPIが誤ったpasswordを拒否することを確認する。"""
    register_response = client.post(
        "/auth/register",
        json={
            "email": "wrong-password@example.com",
            "name": "Wrong Password User",
            "password": "password123",
        },
    )

    response = client.post(
        "/auth/login",
        json={
            "email": "wrong-password@example.com",
            "password": "wrong-password",
        },
    )

    assert register_response.status_code == 201
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
