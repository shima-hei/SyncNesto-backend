"""ユーザー管理APIのテスト。"""

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token, verify_password
from app.models.user import User


def authorize_as(
    client: TestClient,
    user: User,
) -> None:
    """TestClientを指定ユーザーとして認証済みにする。

    Args:
        client: テスト用FastAPIクライアント。
        user: 認証対象ユーザー。
    """
    access_token = create_access_token(subject=user.email)
    client.cookies.set(settings.auth_cookie_name, access_token)


def test_create_user_returns_created_user(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """管理者がユーザーを作成できることを確認する。"""
    admin_user = create_test_user(email="admin@example.com", is_admin=True)
    authorize_as(client, admin_user)

    response = client.post(
        "/users",
        json={
            "email": "user@example.com",
            "name": "User Name",
            "password": "password123",
        },
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": 2,
        "email": "user@example.com",
        "name": "User Name",
    }
    assert "password" not in response.json()
    assert "hashed_password" not in response.json()


def test_create_user_stores_hashed_password(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """作成APIがハッシュ済みpasswordを保存することを確認する。"""
    admin_user = create_test_user(email="admin@example.com", is_admin=True)
    authorize_as(client, admin_user)
    plain_password = "password123"

    response = client.post(
        "/users",
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


def test_create_user_rejects_duplicate_email(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """ユーザー作成APIが重複emailを拒否することを確認する。"""
    admin_user = create_test_user(email="admin@example.com", is_admin=True)
    create_test_user(email="duplicate@example.com")
    authorize_as(client, admin_user)

    response = client.post(
        "/users",
        json={
            "email": "duplicate@example.com",
            "name": "Duplicate User",
            "password": "password123",
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "message": "Email already registered",
        "code": "EMAIL_ALREADY_REGISTERED",
    }


def test_create_user_requires_password(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """作成APIがpassword未指定時に422を返すことを確認する。"""
    admin_user = create_test_user(email="admin@example.com", is_admin=True)
    authorize_as(client, admin_user)

    response = client.post(
        "/users",
        json={
            "email": "missing-password@example.com",
            "name": "Missing Password User",
        },
    )

    assert response.status_code == 422


def test_create_user_requires_login(client: TestClient) -> None:
    """作成APIが未ログインユーザーを拒否することを確認する。"""
    response = client.post(
        "/users",
        json={
            "email": "anonymous@example.com",
            "name": "Anonymous User",
            "password": "password123",
        },
    )

    assert response.status_code == 401
    assert response.json() == {
        "message": "Invalid email or password",
        "code": "INVALID_CREDENTIALS",
    }


def test_create_user_requires_admin(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """作成APIが一般ユーザーを拒否することを確認する。"""
    normal_user = create_test_user(email="normal@example.com", is_admin=False)
    authorize_as(client, normal_user)

    response = client.post(
        "/users",
        json={
            "email": "forbidden@example.com",
            "name": "Forbidden User",
            "password": "password123",
        },
    )

    assert response.status_code == 403
    assert response.json() == {
        "message": "Forbidden",
        "code": "FORBIDDEN",
    }
