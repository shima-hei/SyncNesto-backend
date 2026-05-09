"""認証関連APIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserLogin, UserLoginResponse, UserRead
from app.services.user import UserService

router = APIRouter(prefix="/auth", tags=["auth"])
user_service = UserService()


@router.post(
    "/login",
    response_model=UserLoginResponse,
    response_model_exclude_none=True,
)
def login_user(
    user_in: UserLogin,
    response: Response,
    db: Session = Depends(get_db),
) -> UserLoginResponse:
    """ユーザーログインを行う。

    Args:
        user_in: ユーザーログインリクエストの入力値。
        response: HTTPレスポンス。
        db: DBセッション。

    Returns:
        ログイン成功レスポンス。
    """
    user = user_service.authenticate_user(db, user_in.email, user_in.password)
    access_token = create_access_token(subject=user.email)
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=access_token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        max_age=settings.access_token_expire_minutes * 60,
    )

    if settings.allow_bearer_token_response:
        return UserLoginResponse(access_token=access_token)

    return UserLoginResponse()


@router.get(
    "/me",
    response_model=UserRead,
)
def read_current_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """現在のログインユーザーを取得する。

    Args:
        current_user: 認証済みユーザー。

    Returns:
        現在のログインユーザー情報。
    """
    return current_user
