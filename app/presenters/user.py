"""ユーザー関連レスポンスのPresenterを定義するモジュール。"""

from app.models.rbac import Role
from app.models.user import User, UserType
from app.schemas.user import (
    CurrentUserRead,
    RoleRead,
    UserListItem,
    UserListResponse,
    UserRead,
    UserSummary,
)
from app.services.storage import StorageService


def get_user_type(user: User) -> UserType:
    """DB上のユーザー区分文字列をschema用Enumへ変換する。"""
    return UserType(user.user_type)


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
        user_type=get_user_type(user),
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
        user_type=get_user_type(user),
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
        user_type=get_user_type(user),
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        system_roles=build_role_reads(system_roles),
    )


def build_user_list_response(
    users: list[User],
    *,
    roles_by_user_id: dict[int, list[Role]],
    storage_service: StorageService,
    total: int,
    page: int,
    page_size: int,
) -> UserListResponse:
    """ユーザー一覧レスポンスを組み立てる。

    Args:
        users: レスポンスへ変換するユーザー一覧。
        roles_by_user_id: ユーザーIDをkeyにしたシステムロール一覧。
        storage_service: avatar_url生成に使用するストレージサービス。
        total: 全件数。
        page: ページ番号。
        page_size: 1ページあたりの件数。

    Returns:
        ユーザー一覧レスポンス。
    """
    return UserListResponse(
        items=[
            build_user_list_item(
                user,
                roles_by_user_id.get(user.id, []),
                storage_service,
            )
            for user in users
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
