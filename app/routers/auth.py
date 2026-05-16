"""認証関連APIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import get_db
from app.models.user import User
from app.repositories.rbac import RbacRepository
from app.schemas.user import CurrentUserRead, RoleRead, UserLogin, UserLoginResponse
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


@router.post(
    "/logout",
    status_code=204,
)
def logout_user(response: Response) -> None:
    """ユーザーログアウトを行う。

    Args:
        response: HTTPレスポンス。

    """
    response.delete_cookie(
        key=settings.auth_cookie_name,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
    )


@router.get(
    "/me",
    response_model=CurrentUserRead,
)
def read_current_user(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CurrentUserRead:
    """現在のログインユーザーを取得する。

    Args:
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        現在のログインユーザー情報。
    """
    system_roles = RbacRepository().list_system_roles_by_user(db, current_user.id)
    return CurrentUserRead(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        version=current_user.version,
        system_roles=[RoleRead(key=role.key, name=role.name) for role in system_roles],
    )
