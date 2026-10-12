"""審査許可と所属の整合を、現在の組織スコープに依存せず取得する。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project import Project, ProjectMember
from app.models.rbac import Role, UserRole
from app.models.review_account import ReviewAccountGrant
from app.models.tenant import Tenant, TenantMember


class ReviewAccountRepository:
    """認証前・MCP認証時にも使える読み取り境界。"""

    def get(self, db: Session, user_id: int) -> ReviewAccountGrant | None:
        """失効済みも返す。期限切れを通常アカウントへ昇格させない。"""
        transaction = db.get_transaction()
        absent = db.info.setdefault("review_grant_absent", {})
        if transaction is not None and absent.get(user_id) is transaction:
            return None
        grant = db.get(ReviewAccountGrant, user_id)
        if grant is None:
            # 新規Identityだけに発行する許可。要求内の通常ユーザー判定を再利用する。
            absent[user_id] = db.get_transaction()
        return grant

    def isolated(self, db: Session, user_id: int, tenant_id: int) -> bool:
        """単独の組織メンバーであり、運営Roleや他組織のProjectを持たない。"""
        connection = db.connection()
        memberships = connection.execute(
            select(TenantMember.tenant_id, Role.key, Tenant.status)
            .join(Role, Role.id == TenantMember.role_id)
            .join(Tenant, Tenant.id == TenantMember.tenant_id)
            .where(
                TenantMember.user_id == user_id,
                TenantMember.status == "active",
                Role.scope == "tenant",
            )
        ).all()
        if memberships != [(tenant_id, "tenant_member", "active")]:
            return False
        if connection.execute(
            select(UserRole.id).where(UserRole.user_id == user_id).limit(1)
        ).first():
            return False
        if connection.execute(
            select(TenantMember.id)
            .where(
                TenantMember.tenant_id == tenant_id,
                TenantMember.status == "active",
                TenantMember.user_id != user_id,
            )
            .limit(1)
        ).first():
            return False
        return not connection.execute(
            select(ProjectMember.id)
            .join(Project, Project.id == ProjectMember.project_id)
            .where(
                ProjectMember.user_id == user_id,
                ProjectMember.deleted_at.is_(None),
                Project.tenant_id != tenant_id,
            )
            .limit(1)
        ).first()
