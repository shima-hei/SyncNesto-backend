"""認証APIのテスト。"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

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
    assert second_response.json() == {"detail": "Email already registered"}


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


def test_login_user_returns_access_token_for_valid_credentials(
    client: TestClient,
) -> None:
    """ログインAPIが正しい認証情報でアクセストークンを返すことを確認する。"""
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
    assert "password" not in response.text
    assert "hashed_password" not in response.text


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
    assert response.json() == {"detail": "Invalid email or password"}


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
    assert response.json() == {"detail": "Invalid email or password"}


def test_login_user_requires_password(client: TestClient) -> None:
    """ログインAPIがpassword未指定時に422を返すことを確認する。"""
    response = client.post(
        "/auth/login",
        json={
            "email": "missing-login-password@example.com",
        },
    )

    assert response.status_code == 422
