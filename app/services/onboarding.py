"""承認後に発行したアカウントの初回パスワード設定。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.exceptions import (
    BadRequestError,
    InitialPasswordExpiredError,
    InitialPasswordReuseError,
    InvalidCredentialsError,
)
from app.core.security import get_password_hash, verify_password
from app.models.user import User
from app.repositories.account_action import AccountActionRepository
from app.services.audit_log import AuditLogService

INITIAL_PASSWORD_LIFETIME = timedelta(days=7)


def mark_initial_password(user: User) -> None:
    """新規発行したIdentityに初回設定と7日の期限を付与する。"""
    user.password_change_required = True
    user.initial_password_expires_at = datetime.now(UTC) + INITIAL_PASSWORD_LIFETIME


def check_initial_password_expiry(user: User) -> None:
    """期限切れの初回パスワードではログイン・初回設定を許可しない。"""
    if user.password_change_required and (
        user.initial_password_expires_at is None
        or user.initial_password_expires_at <= datetime.now(UTC)
    ):
        raise InitialPasswordExpiredError()


class OnboardingService:
    """本人の初回設定とセッション・確認リンクの失効を原子的に確定する。"""

    def complete(
        self, db: Session, user_id: int, current_password: str, password: str
    ) -> None:
        """Identityをロックして初回パスワードを再検証し、本人の設定を保存する。"""
        repository = AccountActionRepository()
        user = repository.get_user(db, user_id)
        if user is None or not verify_password(current_password, user.hashed_password):
            raise InvalidCredentialsError()
        if not user.password_change_required:
            raise BadRequestError("初回パスワードの設定は完了しています")
        check_initial_password_expiry(user)
        if verify_password(password, user.hashed_password):
            raise InitialPasswordReuseError()
        user.hashed_password = get_password_hash(password)
        user.password_change_required = False
        user.initial_password_expires_at = None
        user.version += 1
        user.updated_by = user.id
        now = datetime.now(UTC)
        repository.revoke_sessions(db, user.id, now)
        repository.revoke_actions(db, user.id, now)
        repository.clear_login_lock(db, user.email)
        db.commit()
        AuditLogService().record(
            db,
            event_type="account.initial_password.completed",
            actor_user_id=user.id,
            target_user_id=user.id,
            resource_type="user",
            resource_id=user.id,
        )
