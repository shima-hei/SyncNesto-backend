"""ユーザー管理APIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_admin_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserRead
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
    _: User = Depends(get_current_admin_user),
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
