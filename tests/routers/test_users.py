"""ユーザー管理APIのテスト。"""

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token, verify_password
from app.models.user import User


class FakeStorageService:
    """テスト用StorageService。"""

    def generate_presigned_url(self, avatar_key: str | None) -> str | None:
        """固定の署名付きURLを返す。"""
        if avatar_key is None:
            return None

        return f"https://example.com/{avatar_key}?signature=test"


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
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
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
        "version": 1,
        "department": None,
        "position": None,
        "avatar_url": None,
        "is_active": True,
        "last_login_at": None,
        "created_by": admin_user.id,
        "updated_by": admin_user.id,
    }
    assert "password" not in response.json()
    assert "hashed_password" not in response.json()


def test_create_user_stores_hashed_password(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """作成APIがハッシュ済みpasswordを保存することを確認する。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
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


def test_create_user_stores_profile_fields_and_audit_users(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """作成APIがプロフィール項目と監査ユーザーを保存することを確認する。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    authorize_as(client, admin_user)

    response = client.post(
        "/users",
        json={
            "email": "profile@example.com",
            "name": "Profile User",
            "password": "password123",
            "department": "QA",
            "position": "Tester",
            "is_active": False,
        },
    )

    assert response.status_code == 201
    assert response.json()["department"] == "QA"
    assert response.json()["position"] == "Tester"
    assert response.json()["avatar_url"] is None
    assert response.json()["is_active"] is False
    assert response.json()["created_by"] == admin_user.id
    assert response.json()["updated_by"] == admin_user.id

    user = db.query(User).filter(User.email == "profile@example.com").one()
    assert user.department == "QA"
    assert user.position == "Tester"
    assert user.avatar_key is None
    assert user.is_active is False
    assert user.created_by == admin_user.id
    assert user.updated_by == admin_user.id


def test_create_user_rejects_duplicate_email(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """ユーザー作成APIが重複emailを拒否することを確認する。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
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
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
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
    normal_user = create_test_user(email="normal@example.com")
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


def test_list_users_returns_users_for_system_admin(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """system_adminがユーザー一覧を取得できることを確認する。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    create_test_user(email="user-a@example.com", name="User A")
    authorize_as(client, admin_user)

    response = client.get("/users")

    assert response.status_code == 200
    assert [user["email"] for user in response.json()] == [
        "admin@example.com",
        "user-a@example.com",
    ]


def test_list_users_returns_presigned_avatar_url(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
    monkeypatch,
) -> None:
    """ユーザー一覧でavatar_keyから署名付きURLを返すことを確認する。"""
    from app.routers import users

    monkeypatch.setattr(users, "storage_service", FakeStorageService())
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    target_user = create_test_user(email="avatar@example.com")
    target_user.avatar_key = "users/2.png"
    db.commit()
    authorize_as(client, admin_user)

    response = client.get("/users")

    assert response.status_code == 200
    assert response.json()[1]["avatar_url"] == (
        "https://example.com/users/2.png?signature=test"
    )


def test_list_users_requires_user_read_permission(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """user:read権限がない場合にユーザー一覧取得を拒否する。"""
    normal_user = create_test_user(email="normal@example.com")
    authorize_as(client, normal_user)

    response = client.get("/users")

    assert response.status_code == 403


def test_read_user_returns_user_for_system_admin(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """system_adminがユーザー詳細を取得できることを確認する。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    target_user = create_test_user(email="target@example.com", name="Target User")
    authorize_as(client, admin_user)

    response = client.get(f"/users/{target_user.id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": target_user.id,
        "email": "target@example.com",
        "name": "Target User",
        "version": 1,
        "department": None,
        "position": None,
        "avatar_url": None,
        "is_active": True,
        "last_login_at": None,
        "created_by": None,
        "updated_by": None,
    }


def test_update_user_updates_user_for_system_admin(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """system_adminがユーザーを更新できることを確認する。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    target_user = create_test_user(email="before@example.com", name="Before")
    authorize_as(client, admin_user)

    response = client.patch(
        f"/users/{target_user.id}",
        json={
            "email": "after@example.com",
            "name": "After",
            "department": "QA",
            "position": "Lead",
            "is_active": False,
            "version": target_user.version,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": target_user.id,
        "email": "after@example.com",
        "name": "After",
        "version": target_user.version + 1,
        "department": "QA",
        "position": "Lead",
        "avatar_url": None,
        "is_active": False,
        "last_login_at": None,
        "created_by": None,
        "updated_by": admin_user.id,
    }


def test_update_user_rejects_stale_version_with_current_user(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """古いversionでのユーザー更新を409で拒否し、最新情報を返す。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    target_user = create_test_user(email="before@example.com", name="Before")
    target_user.name = "Latest"
    target_user.version = 2
    db.commit()
    db.refresh(target_user)
    authorize_as(client, admin_user)

    response = client.patch(
        f"/users/{target_user.id}",
        json={"name": "Stale", "version": 1},
    )

    assert response.status_code == 409
    assert response.json() == {
        "message": "Resource version conflict",
        "code": "VERSION_CONFLICT",
        "current": {
            "id": target_user.id,
            "email": "before@example.com",
            "name": "Latest",
            "version": 2,
            "department": None,
            "position": None,
            "avatar_url": None,
            "is_active": True,
            "last_login_at": None,
            "created_by": None,
            "updated_by": None,
        },
    }


def test_update_user_requires_version(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """ユーザー更新時にversionが必須であることを確認する。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    target_user = create_test_user(email="target@example.com")
    authorize_as(client, admin_user)

    response = client.patch(
        f"/users/{target_user.id}",
        json={"name": "Updated"},
    )

    assert response.status_code == 422


def test_update_user_requires_user_update_permission(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """user:update権限がない場合にユーザー更新を拒否する。"""
    normal_user = create_test_user(email="normal@example.com")
    target_user = create_test_user(email="target@example.com")
    authorize_as(client, normal_user)

    response = client.patch(
        f"/users/{target_user.id}",
        json={"name": "Updated", "version": target_user.version},
    )

    assert response.status_code == 403


def test_delete_user_soft_deletes_user_for_system_admin(
    client: TestClient,
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """system_adminがユーザーを論理削除できることを確認する。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    target_user = create_test_user(email="delete@example.com")
    authorize_as(client, admin_user)

    response = client.delete(f"/users/{target_user.id}")

    assert response.status_code == 204
    db.refresh(target_user)
    assert target_user.deleted_at is not None


def test_delete_user_requires_user_delete_permission(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """user:delete権限がない場合にユーザー削除を拒否する。"""
    normal_user = create_test_user(email="normal@example.com")
    target_user = create_test_user(email="target@example.com")
    authorize_as(client, normal_user)

    response = client.delete(f"/users/{target_user.id}")

    assert response.status_code == 403
