"""ユーザー管理APIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.auth import require_system_permission
from app.db.session import get_db
from app.models.user import User
from app.presenters.user import build_user_list_item, build_user_response
from app.schemas.user import (
    UserCreate,
    UserListResponse,
    UserRead,
    UserUpdate,
)
from app.services.storage import StorageService
from app.services.user import UserService


router = APIRouter(prefix="/users", tags=["users"])
user_service = UserService()
storage_service = StorageService()


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
def create_user(
    user_in: UserCreate,
    current_user: User = Depends(require_system_permission("user:create")),
    db: Session = Depends(get_db),
) -> UserRead:
    """管理者としてユーザーを作成する。

    Args:
        user_in: ユーザー作成リクエストの入力値。
        current_user: 認可済みユーザー。
        db: DBセッション。

    Returns:
        作成されたユーザー情報。
    """
    user = user_service.create_user(db, user_in, actor_id=current_user.id)
    system_roles = user_service.list_system_roles_by_user(db, user.id)
    return build_user_response(user, system_roles, storage_service)


@router.get(
    "",
    response_model=UserListResponse,
)
def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    _: User = Depends(require_system_permission("user:read")),
    db: Session = Depends(get_db),
) -> UserListResponse:
    """ユーザー一覧を取得する。

    Args:
        page: ページ番号。
        page_size: 1ページあたりの件数。
        q: 検索キーワード。
        is_active: 有効状態の絞り込み。
        db: DBセッション。

    Returns:
        ユーザー一覧。
    """
    users, total = user_service.list_users_paginated(
        db,
        page=page,
        page_size=page_size,
        q=q,
        is_active=is_active,
    )
    roles_by_user_id = user_service.list_system_roles_by_user_ids(
        db,
        [user.id for user in users],
    )
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


@router.get(
    "/{user_id}",
    response_model=UserRead,
)
def read_user(
    user_id: int,
    _: User = Depends(require_system_permission("user:read")),
    db: Session = Depends(get_db),
) -> UserRead:
    """ユーザーを取得する。

    Args:
        user_id: 取得対象ユーザーID。
        db: DBセッション。

    Returns:
        取得されたユーザー。
    """
    user = user_service.get_user(db, user_id)
    system_roles = user_service.list_system_roles_by_user(db, user.id)
    return build_user_response(user, system_roles, storage_service)


@router.patch(
    "/{user_id}",
    response_model=UserRead,
)
def update_user(
    user_id: int,
    user_in: UserUpdate,
    current_user: User = Depends(require_system_permission("user:update")),
    db: Session = Depends(get_db),
) -> UserRead:
    """ユーザーを更新する。

    Args:
        user_id: 更新対象ユーザーID。
        user_in: ユーザー更新リクエストの入力値。
        current_user: 認可済みユーザー。
        db: DBセッション。

    Returns:
        更新されたユーザー。
    """
    user = user_service.update_user(db, user_id, user_in, actor_id=current_user.id)
    system_roles = user_service.list_system_roles_by_user(db, user.id)
    return build_user_response(user, system_roles, storage_service)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_user(
    user_id: int,
    _: User = Depends(require_system_permission("user:delete")),
    db: Session = Depends(get_db),
) -> None:
    """ユーザーを論理削除する。

    Args:
        user_id: 削除対象ユーザーID。
        db: DBセッション。
    """
    user_service.delete_user(db, user_id)
