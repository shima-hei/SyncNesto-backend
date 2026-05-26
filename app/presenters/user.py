"""ユーザー関連レスポンスのPresenterを定義するモジュール。"""

from app.models.rbac import Role
from app.models.user import User
from app.schemas.user import (
    CurrentUserRead,
    RoleRead,
    UserListItem,
    UserRead,
    UserSummary,
)
from app.services.storage import StorageService


def build_role_reads(roles: list[Role]) -> list[RoleRead]:
    """ロールモデルをレスポンスschemaへ変換する。

    Args:
        roles: ロール一覧。

    Returns:
        ロール読み取りレスポンス一覧。
    """
    return [RoleRead(key=role.key, name=role.name) for role in roles]


def build_user_summary(user: User, storage_service: StorageService) -> UserSummary:
    """ユーザーモデルを軽量ユーザーschemaへ変換する。

    Args:
        user: レスポンスへ変換するユーザー。
        storage_service: avatar_url生成に使用するストレージサービス。

    Returns:
        軽量ユーザーレスポンス。
    """
    return UserSummary(
        id=user.id,
        email=user.email,
        name=user.name,
        avatar_url=storage_service.generate_presigned_url(user.avatar_key),
        is_active=user.is_active,
    )


def build_user_response(
    user: User,
    system_roles: list[Role],
    storage_service: StorageService,
) -> UserRead:
    """ユーザーレスポンスを組み立てる。

    Args:
        user: レスポンスへ変換するユーザー。
        system_roles: ユーザーに付与されたシステムロール一覧。
        storage_service: avatar_url生成に使用するストレージサービス。

    Returns:
        ユーザー読み取りレスポンス。
    """
    return UserRead(
        id=user.id,
        email=user.email,
        name=user.name,
        version=user.version,
        department=user.department,
        position=user.position,
        avatar_url=storage_service.generate_presigned_url(user.avatar_key),
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_by=user.created_by,
        updated_by=user.updated_by,
        system_roles=build_role_reads(system_roles),
    )


def build_current_user_response(
    user: User,
    system_roles: list[Role],
    storage_service: StorageService,
) -> CurrentUserRead:
    """現在のログインユーザーレスポンスを組み立てる。

    Args:
        user: レスポンスへ変換するユーザー。
        system_roles: ユーザーに付与されたシステムロール一覧。
        storage_service: avatar_url生成に使用するストレージサービス。

    Returns:
        現在のログインユーザー読み取りレスポンス。
    """
    return CurrentUserRead.model_validate(
        build_user_response(user, system_roles, storage_service)
    )


def build_user_list_item(
    user: User,
    system_roles: list[Role],
    storage_service: StorageService,
) -> UserListItem:
    """ユーザー一覧itemレスポンスを組み立てる。

    Args:
        user: レスポンスへ変換するユーザー。
        system_roles: ユーザーに付与されたシステムロール一覧。
        storage_service: avatar_url生成に使用するストレージサービス。

    Returns:
        ユーザー一覧itemレスポンス。
    """
    return UserListItem(
        id=user.id,
        email=user.email,
        name=user.name,
        department=user.department,
        position=user.position,
        avatar_url=storage_service.generate_presigned_url(user.avatar_key),
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        system_roles=build_role_reads(system_roles),
    )
