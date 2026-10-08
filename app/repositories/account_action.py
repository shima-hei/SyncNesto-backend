"""本人確認要求の取得・失効。commitはServiceの同一トランザクションで行う。"""

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.account_action import AccountAction
from app.models.login_attempt import LoginAttempt
from app.models.rbac import Role
from app.models.session import UserSession
from app.models.tenant import Tenant, TenantMember
from app.models.user import User


class AccountActionRepository:
    """Identityを先にロックしてトークン間の並行変更を直列化する。"""

    def user_id_by_email(self, db: Session, email: str) -> int | None:
        """公開申請用に有効なIdentityのIDだけを読む。"""
        return db.scalar(
            db.query(User.id)
            .filter(
                User.email == email, User.is_active.is_(True), User.deleted_at.is_(None)
            )
            .statement
        )

    def email_in_use(self, db: Session, email: str) -> bool:
        """削除済みIdentityも含むDBの一意性と同じ条件で確認する。"""
        return db.query(User.id).filter(User.email == email).first() is not None

    def get_user(self, db: Session, user_id: int) -> User | None:
        """同じユーザーの要求・確認処理すべてで共通のロックを使う。"""
        return (
            db.query(User)
            .filter(
                User.id == user_id, User.deleted_at.is_(None), User.is_active.is_(True)
            )
            .populate_existing()
            .with_for_update()
            .first()
        )

    def by_hash(
        self, db: Session, digest: str, *, lock: bool = False
    ) -> AccountAction | None:
        """ハッシュから要求を取得する。Identityロック後に必要な行をロックする。"""
        query = db.query(AccountAction).filter(AccountAction.token_hash == digest)
        if lock:
            query = query.populate_existing().with_for_update()
        return query.first()

    def tenant_role(self, db: Session, tenant_id: int, user_id: int) -> str | None:
        """有効な組織・所属・Identityの組織権限を読む。"""
        return db.scalar(
            db.query(Role.key)
            .join(TenantMember, TenantMember.role_id == Role.id)
            .join(Tenant, Tenant.id == TenantMember.tenant_id)
            .join(User, User.id == TenantMember.user_id)
            .filter(
                Tenant.id == tenant_id,
                Tenant.status == "active",
                TenantMember.user_id == user_id,
                TenantMember.status == "active",
                Role.scope == "tenant",
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .statement
        )

    def revoke_actions(self, db: Session, user_id: int, now: datetime) -> None:
        """本人確認が完了したら他の未使用リンクをすべて失効する。"""
        db.execute(
            update(AccountAction)
            .where(
                AccountAction.user_id == user_id,
                AccountAction.consumed_at.is_(None),
                AccountAction.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

    def revoke_sessions(self, db: Session, user_id: int, now: datetime) -> None:
        """共有ログイン情報の変更と全ログイン失効を同時に確定する。"""
        db.execute(
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason="credentials_changed")
        )

    def clear_login_lock(self, db: Session, email: str) -> None:
        """メール本人確認完了後に、以前の失敗回数によるログインロックを解除する。"""
        db.execute(
            update(LoginAttempt)
            .where(LoginAttempt.email == email.lower())
            .values(failed_count=0, locked_until=None, last_failed_at=None)
        )
