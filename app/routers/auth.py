"""認証関連APIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Cookie, Depends, File, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.auth import get_authenticated_user, get_current_user
from app.core.auth_cookie import delete_auth_cookie, set_auth_cookie
from app.core.config import settings
from app.core.csrf import delete_csrf_cookie, generate_csrf_token, set_csrf_cookie
from app.db.session import get_db, get_normal_db
from app.models.rbac import Role
from app.models.user import User
from app.presenters.user import build_current_user_response
from app.schemas.account_action import AccountActionMessage
from app.schemas.file_upload import (
    FileUploadComplete,
    FileUploadPlan,
    FileUploadRequest,
)
from app.schemas.user import (
    CurrentUserRead,
    InitialPasswordConfirm,
    UserLogin,
    UserLoginResponse,
    UserProfileUpdate,
)
from app.services.audit_log import AuditLogService
from app.services.demo import DemoService
from app.services.onboarding import OnboardingService
from app.services.session import SessionService
from app.services.storage import MAX_IMAGE_BYTES, StorageService
from app.services.user import UserService

router = APIRouter(prefix="/auth", tags=["auth"])
user_service = UserService()
session_service = SessionService()
storage_service = StorageService()
audit_log_service = AuditLogService()

SESSION_REVOKE_REASON_LOGOUT = "logout"


def current_user_response(
    db: Session, user: User, roles: list[Role]
) -> CurrentUserRead:
    """本人更新でもデモの利用状態と短期URLを維持する。"""
    result = build_current_user_response(user, roles, storage_service.for_demo(db))
    if demo_id := db.info.get("demo_id"):
        demo = DemoService().repository.lock(db, demo_id)
        assert demo is not None
        result.demo = DemoService().status(demo)
    return result


@router.post(
    "/login",
    response_model=UserLoginResponse,
    response_model_exclude_none=True,
)
def login_user(
    user_in: UserLogin,
    response: Response,
    db: Session = Depends(get_normal_db),
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
    user_service.update_last_login_at(db, user, commit=False)
    user_session, access_token = session_service.create_session_token(db, user)
    audit_log_service.record_login_success(
        db,
        user=user,
        user_session=user_session,
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
            password_change_required=user.password_change_required,
            access_token=access_token,
            token_type="bearer",
        )

    return UserLoginResponse(
        message="Login successful",
        password_change_required=user.password_change_required,
    )


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
        audit_log_service.record_logout(
            db,
            user_session=user_session,
        )
        if demo_id := db.info.get("demo_cleanup_id"):
            DemoService().cleanup(demo_id)

    delete_auth_cookie(response)
    delete_csrf_cookie(response)


@router.get(
    "/me",
    response_model=CurrentUserRead,
)
def read_current_user(
    current_user: User = Depends(get_authenticated_user),
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
    return current_user_response(db, current_user, system_roles)


@router.post("/initial-password", response_model=AccountActionMessage)
def complete_initial_password(
    data: InitialPasswordConfirm,
    response: Response,
    user: User = Depends(get_authenticated_user),
    db: Session = Depends(get_db),
) -> AccountActionMessage:
    """初回ログインの本人が設定を完了し、全セッションを失効する。"""
    OnboardingService().complete(db, user.id, data.current_password, data.password)
    delete_auth_cookie(response)
    delete_csrf_cookie(response)
    return AccountActionMessage(
        message="パスワードを設定しました。新しいパスワードでログインしてください"
    )


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
    return current_user_response(db, user, system_roles)


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
        content=file.file.read(MAX_IMAGE_BYTES + 1),
        content_type=file.content_type,
        storage_service=storage_service,
    )
    system_roles = user_service.list_system_roles_by_user(db, user.id)
    return current_user_response(db, user, system_roles)


@router.post("/me/avatar/upload-plan", response_model=FileUploadPlan)
def plan_current_user_avatar_upload(
    data: FileUploadRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileUploadPlan:
    """本人のアイコン送信方式を返す。"""
    return user_service.plan_avatar_upload(
        current_user=current_user, data=data, storage_service=storage_service, db=db
    )


@router.post("/me/avatar/upload-complete", response_model=CurrentUserRead)
def complete_current_user_avatar_upload(
    data: FileUploadComplete,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CurrentUserRead:
    """本人が直接送信したアイコンを検証して登録する。"""
    user = user_service.complete_avatar_upload(
        db,
        current_user=current_user,
        token=data.upload_token,
        storage_service=storage_service,
    )
    system_roles = user_service.list_system_roles_by_user(db, user.id)
    return current_user_response(db, user, system_roles)


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
    return current_user_response(db, user, system_roles)
