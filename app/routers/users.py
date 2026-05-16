"""ユーザー管理APIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.auth import require_system_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserRead, UserUpdate
from app.services.user import UserService


router = APIRouter(prefix="/users", tags=["users"])
user_service = UserService()


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
def create_user(
    user_in: UserCreate,
    _: User = Depends(require_system_permission("user:create")),
    db: Session = Depends(get_db),
) -> User:
    """管理者としてユーザーを作成する。

    Args:
        user_in: ユーザー作成リクエストの入力値。
        db: DBセッション。

    Returns:
        作成されたユーザー情報。
    """
    return user_service.create_user(db, user_in)


@router.get(
    "",
    response_model=list[UserRead],
)
def list_users(
    _: User = Depends(require_system_permission("user:read")),
    db: Session = Depends(get_db),
) -> list[User]:
    """ユーザー一覧を取得する。

    Args:
        db: DBセッション。

    Returns:
        ユーザー一覧。
    """
    return user_service.list_users(db)


@router.get(
    "/{user_id}",
    response_model=UserRead,
)
def read_user(
    user_id: int,
    _: User = Depends(require_system_permission("user:read")),
    db: Session = Depends(get_db),
) -> User:
    """ユーザーを取得する。

    Args:
        user_id: 取得対象ユーザーID。
        db: DBセッション。

    Returns:
        取得されたユーザー。
    """
    return user_service.get_user(db, user_id)


@router.patch(
    "/{user_id}",
    response_model=UserRead,
)
def update_user(
    user_id: int,
    user_in: UserUpdate,
    _: User = Depends(require_system_permission("user:update")),
    db: Session = Depends(get_db),
) -> User:
    """ユーザーを更新する。

    Args:
        user_id: 更新対象ユーザーID。
        user_in: ユーザー更新リクエストの入力値。
        db: DBセッション。

    Returns:
        更新されたユーザー。
    """
    return user_service.update_user(db, user_id, user_in)


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
