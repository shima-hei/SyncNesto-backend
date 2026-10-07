"""本人確認リンクの公開APIと本人によるメール変更申請。"""

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.logging import client_ip_context
from app.db.session import get_db
from app.models.user import User
from app.schemas.account_action import (
    AccountActionInspection,
    AccountActionMessage,
    AccountActionToken,
    EmailChangeRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
)
from app.services.account_action import AccountActionService
from app.services.request_limit import consume_email_request_budget

router = APIRouter(prefix="/auth", tags=["account-actions"])
service = AccountActionService()


@router.post(
    "/password-reset/request", response_model=AccountActionMessage, status_code=202
)
def request_password_reset(data: PasswordResetRequest, background: BackgroundTasks):
    """存在の有無に依存しない応答後、登録先メールへの案内を送信する。"""
    if settings.app_env == "demo":
        return AccountActionMessage(message="デモのためメールは送信しません")
    service.email_service.ensure_available()
    consume_email_request_budget(str(data.email), client_ip_context.get() or "unknown")
    background.add_task(service.public_password_reset, str(data.email))
    return AccountActionMessage(
        message="登録されている場合、パスワード再設定メールを送信します。受信箱を確認してください"
    )


@router.post("/account-actions/inspect", response_model=AccountActionInspection)
def inspect_account_action(data: AccountActionToken, db: Session = Depends(get_db)):
    """表示だけでは確認リンクを使用しない。"""
    return service.inspect(db, data.token)


@router.post("/password-reset/confirm", response_model=AccountActionMessage)
def confirm_password_reset(
    data: PasswordResetConfirm,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """未ログインの本人が新しいパスワードを設定する。"""
    email = service.reset_password(db, data.token, data.password)
    background.add_task(service.notify_credentials_changed, email)
    return AccountActionMessage(
        message="パスワードを変更しました。新しいパスワードでログインしてください"
    )


@router.post(
    "/email-change/request", response_model=AccountActionMessage, status_code=202
)
def request_my_email_change(
    data: EmailChangeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """本人が現在のメールでの承認を申請する。"""
    consume_email_request_budget(
        f"user:{user.id}", client_ip_context.get() or "unknown"
    )
    service.request_email_change(
        db, user_id=user.id, actor_id=user.id, new_email=str(data.new_email)
    )
    return AccountActionMessage(
        message="デモのためメールは送信しません"
        if db.info.get("demo_id")
        else "現在のメールアドレスに承認メールを送信しました"
    )


@router.post("/email-change/approve", response_model=AccountActionMessage)
def approve_email_change(data: AccountActionToken, db: Session = Depends(get_db)):
    """旧メールの受信者が承認すると、新メールへ確認を送信する。"""
    service.approve_email_change(db, data.token)
    return AccountActionMessage(
        message="承認しました。新しいメールアドレスに届いた確認メールを開いてください"
    )


@router.post("/email-change/confirm", response_model=AccountActionMessage)
def confirm_email_change(
    data: AccountActionToken,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """新メールの受信者が確認して初めて変更を確定する。"""
    old_email, new_email = service.confirm_email_change(db, data.token)
    background.add_task(service.notify_credentials_changed, old_email)
    background.add_task(service.notify_credentials_changed, new_email)
    return AccountActionMessage(
        message="メールアドレスを変更しました。新しいアドレスでログインしてください"
    )
