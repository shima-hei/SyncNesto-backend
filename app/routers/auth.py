"""認証関連APIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserLogin, UserLoginResponse, UserRead
from app.services.user import UserService

router = APIRouter(prefix="/auth", tags=["auth"])
user_service = UserService()


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
def register_user(
    user_in: UserCreate,
    db: Session = Depends(get_db),
) -> User:
    """ユーザー登録を行う。

    Args:
        user_in: ユーザー登録リクエストの入力値。
        db: DBセッション。

    Returns:
        作成されたユーザー情報。
    """
    return user_service.create_user(db, user_in)


@router.post(
    "/login",
    response_model=UserLoginResponse,
)
def login_user(
    user_in: UserLogin,
    db: Session = Depends(get_db),
) -> UserLoginResponse:
    """ユーザーログインを行う。

    Args:
        user_in: ユーザーログインリクエストの入力値。
        db: DBセッション。

    Returns:
        ログイン成功レスポンス。
    """
    user_service.authenticate_user(db, user_in.email, user_in.password)
    return UserLoginResponse()
