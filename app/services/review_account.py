"""審査専用Identityの例外を通常のMFA・RBACから分離する。"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError
from app.models.review_account import ReviewAccountGrant
from app.models.user import User
from app.repositories.review_account import ReviewAccountRepository

# Roleを変更されても、運営・組織・Projectメンバー管理を審査Identityへ開放しない。
REVIEW_PERMISSIONS = frozenset(
    {
        "mcp:connect",
        "project:read",
        "requirement:read",
        "requirement:create",
        "requirement:update",
        "requirement:comment",
        "requirement:review",
        "requirement:link",
        "test_plan:read",
        "test_plan:create",
        "test_plan:update",
        "test_plan:comment",
        "test_case:read",
        "test_case:create",
        "test_case:update",
        "test_case:execute",
        "task:read",
        "task:create",
        "task:update",
        "task:delete",
        "task:comment",
    }
)


class ReviewAccountService:
    """運営CLIで発行した専用Identityだけをサンプル組織へ固定する。"""

    def grant(self, db: Session, user: User) -> ReviewAccountGrant | None:
        """通常Identityと匿名デモには例外を適用しない。"""
        if db.info.get("data_realm") == "demo" or db.info.get("demo_id"):
            return None
        return ReviewAccountRepository().get(db, user.id)

    def validate(self, db: Session, user: User) -> ReviewAccountGrant | None:
        """各認証で期限と隔離を再検証する。異常時は利用全体を拒否する。"""
        grant = self.grant(db, user)
        if grant is None:
            return None
        if (
            grant.revoked_at is not None
            or grant.expires_at <= datetime.now(UTC)
            or not user.is_active
            or user.deleted_at is not None
            or user.password_change_required
            or user.mfa_recovery_pending
            or not ReviewAccountRepository().isolated(db, user.id, grant.tenant_id)
        ):
            raise ForbiddenError()
        return grant

    def reject_identity_change(self, db: Session, user: User) -> None:
        """共有審査資格情報の変更・回復は運営CLIに限定する。"""
        if self.grant(db, user) is not None:
            raise ForbiddenError()
