"""認証関連APIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Cookie, Depends, File, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.auth_cookie import delete_auth_cookie, set_auth_cookie
from app.core.config import settings
from app.core.csrf import delete_csrf_cookie, generate_csrf_token, set_csrf_cookie
from app.db.session import get_db
from app.models.user import User
from app.presenters.user import build_current_user_response
from app.schemas.user import (
    CurrentUserRead,
    UserLogin,
    UserLoginResponse,
    UserProfileUpdate,
)
from app.services.audit_log import AuditEventType, AuditLogService
from app.services.session import SessionService
from app.services.storage import StorageService
from app.services.user import UserService

router = APIRouter(prefix="/auth", tags=["auth"])
user_service = UserService()
session_service = SessionService()
storage_service = StorageService()
audit_log_service = AuditLogService()

SESSION_REVOKE_REASON_LOGOUT = "logout"


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
    user_service.update_last_login_at(db, user)
    user_session, access_token = session_service.create_session_token(db, user)
    audit_log_service.record(
        db,
        event_type=AuditEventType.AUTH_LOGIN_SUCCESS,
        actor_user_id=user.id,
        target_user_id=user.id,
        resource_type="session",
        resource_id=None,
        metadata={"session_id": str(user_session.id), "email": user.email},
    )
    set_auth_cookie(
        response,
        access_token,
        max_age_seconds=settings.session_idle_timeout_minutes * 60,
    )
    set_csrf_cookie(response, generate_csrf_token())

    if settings.allow_bearer_token_response:
        return UserLoginResponse(
            message="Login successful",
            access_token=access_token,
            token_type="bearer",
        )

    return UserLoginResponse(message="Login successful")


@router.post(
    "/logout",
    status_code=204,
)
def logout_user(
    response: Response,
    access_token: str | None = Cookie(default=None, alias=settings.auth_cookie_name),
    db: Session = Depends(get_db),
) -> None:
    """ユーザーログアウトを行う。

    Args:
        response: HTTPレスポンス。
        access_token: 認証Cookieに含まれるアクセストークン。
        db: DBセッション。
    """
    if access_token is not None:
        user_session = session_service.revoke_token_session(
            db,
            access_token,
            SESSION_REVOKE_REASON_LOGOUT,
        )
        audit_log_service.record(
            db,
            event_type=AuditEventType.AUTH_LOGOUT,
            actor_user_id=user_session.user_id if user_session is not None else None,
            target_user_id=user_session.user_id if user_session is not None else None,
            resource_type="session",
            resource_id=None,
            metadata={
                "session_id": str(user_session.id)
                if user_session is not None
                else None,
            },
        )

    delete_auth_cookie(response)
    delete_csrf_cookie(response)


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
    system_roles = user_service.list_system_roles_by_user(db, current_user.id)
    return build_current_user_response(current_user, system_roles, storage_service)


@router.patch(
    "/me",
    response_model=CurrentUserRead,
)
def update_current_user(
    user_in: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CurrentUserRead:
    """現在のログインユーザーのプロフィールを更新する。

    Args:
        user_in: 本人プロフィール更新リクエストの入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        更新された現在のログインユーザー情報。
    """
    user = user_service.update_profile(db, current_user=current_user, user_in=user_in)
    system_roles = user_service.list_system_roles_by_user(db, user.id)
    return build_current_user_response(user, system_roles, storage_service)


@router.put(
    "/me/avatar",
    response_model=CurrentUserRead,
)
def update_current_user_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CurrentUserRead:
    """現在のログインユーザーのアイコン画像を更新する。

    Args:
        file: アップロードされた画像ファイル。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        更新された現在のログインユーザー情報。
    """
    user = user_service.update_avatar(
        db,
        current_user=current_user,
        content=file.file.read(),
        content_type=file.content_type,
        storage_service=storage_service,
    )
    system_roles = user_service.list_system_roles_by_user(db, user.id)
    return build_current_user_response(user, system_roles, storage_service)


@router.delete(
    "/me/avatar",
    response_model=CurrentUserRead,
)
def delete_current_user_avatar(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CurrentUserRead:
    """現在のログインユーザーのアイコン画像を削除する。

    Args:
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        更新された現在のログインユーザー情報。
    """
    user = user_service.delete_avatar(
        db,
        current_user=current_user,
        storage_service=storage_service,
    )
    system_roles = user_service.list_system_roles_by_user(db, user.id)
    return build_current_user_response(user, system_roles, storage_service)
