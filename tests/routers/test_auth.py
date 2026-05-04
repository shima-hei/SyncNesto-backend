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
