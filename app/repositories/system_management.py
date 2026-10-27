"""運営用メタデータを組織選択と独立して読み取る。"""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.rbac import Role
from app.models.tenant import Tenant, TenantMember
from app.models.user import User


class SystemManagementRepository:
    """既存の組織・所属・Identityから運営用情報を取得する。"""

    def summary(self, db: Session) -> dict[str, int]:
        """削除済みIdentityを除き、組織とユーザーを集計する。"""
        tenants = db.query(
            func.count(Tenant.id),
            func.count(Tenant.id).filter(Tenant.status == "active"),
            func.count(Tenant.id).filter(Tenant.status != "active"),
        ).one()
        active_membership = (
            select(TenantMember.id)
            .join(Tenant, Tenant.id == TenantMember.tenant_id)
            .where(
                TenantMember.user_id == User.id,
                TenantMember.status == "active",
                Tenant.status == "active",
            )
            .exists()
        )
        users = (
            db.query(
                func.count(User.id),
                func.count(User.id).filter(User.is_active.is_(True)),
                func.count(User.id).filter(User.is_active.is_(False)),
                func.count(User.id).filter(~active_membership),
            )
            .filter(User.deleted_at.is_(None))
            .one()
        )
        return dict(
            zip(
                (
                    "total_tenants",
                    "active_tenants",
                    "suspended_tenants",
                    "total_users",
                    "active_users",
                    "inactive_users",
                    "users_without_active_membership",
                ),
                (*tenants, *users),
                strict=True,
            )
        )

    def tenant(self, db: Session, tenant_id: int) -> Tenant | None:
        """停止中を含む指定組織を取得する。"""
        return db.query(Tenant).filter(Tenant.id == tenant_id).first()

    def tenants(
        self,
        db: Session,
        *,
        page: int,
        page_size: int,
        q: str | None,
        status: str | None,
    ) -> tuple[list[Tenant], int]:
        """組織名・識別子で検索してページを取得する。"""
        query = db.query(Tenant)
        if q:
            pattern = f"%{q}%"
            query = query.filter(
                or_(Tenant.name.ilike(pattern), Tenant.slug.ilike(pattern))
            )
        if status:
            query = query.filter(Tenant.status == status)
        total = query.count()
        return query.order_by(Tenant.id).offset((page - 1) * page_size).limit(
            page_size
        ).all(), total

    def tenant_metadata(self, db: Session, tenant_ids: list[int]):
        """ページ内の人数とOwnerをまとめて取得し、組織ごとのクエリを避ける。"""
        if not tenant_ids:
            return [], []
        query = (
            db.query(
                TenantMember.tenant_id,
                func.count(TenantMember.id),
                func.count(TenantMember.id).filter(
                    and_(TenantMember.status == "active", User.is_active.is_(True))
                ),
            )
            .join(User, User.id == TenantMember.user_id)
            .filter(
                TenantMember.tenant_id.in_(tenant_ids),
                TenantMember.status != "removed",
                User.deleted_at.is_(None),
            )
            .group_by(TenantMember.tenant_id)
        )
        owners = (
            db.query(TenantMember, User)
            .join(User, User.id == TenantMember.user_id)
            .join(Role, Role.id == TenantMember.role_id)
            .filter(
                TenantMember.tenant_id.in_(tenant_ids),
                TenantMember.status == "active",
                User.deleted_at.is_(None),
                User.is_active.is_(True),
                Role.scope == "tenant",
                Role.key == "tenant_owner",
            )
            .order_by(TenantMember.id)
            .all()
        )
        return query.all(), owners

    def members(
        self,
        db: Session,
        tenant_id: int,
        *,
        page: int,
        page_size: int,
        q: str | None,
        status: str | None,
    ):
        """指定組織だけの所属ユーザーを検索・ページ分割する。"""
        query = (
            db.query(TenantMember, User, Role)
            .join(User, User.id == TenantMember.user_id)
            .join(Role, Role.id == TenantMember.role_id)
            .filter(
                TenantMember.tenant_id == tenant_id,
                TenantMember.status != "removed",
                User.deleted_at.is_(None),
                Role.scope == "tenant",
            )
        )
        if q:
            pattern = f"%{q}%"
            query = query.filter(
                or_(
                    TenantMember.display_name.ilike(pattern),
                    User.name.ilike(pattern),
                    User.email.ilike(pattern),
                    TenantMember.department.ilike(pattern),
                    TenantMember.position.ilike(pattern),
                )
            )
        if status:
            query = query.filter(TenantMember.status == status)
        total = query.count()
        return query.order_by(TenantMember.id).offset((page - 1) * page_size).limit(
            page_size
        ).all(), total

    def user(self, db: Session, user_id: int) -> User | None:
        """削除済みを除くIdentityの存在を確認する。"""
        return (
            db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
        )

    def memberships(self, db: Session, user_id: int):
        """停止中を含め、指定Identityの削除されていない所属を取得する。"""
        return (
            db.query(Tenant, TenantMember, Role)
            .join(TenantMember, TenantMember.tenant_id == Tenant.id)
            .join(Role, Role.id == TenantMember.role_id)
            .filter(
                TenantMember.user_id == user_id,
                TenantMember.status != "removed",
                Role.scope == "tenant",
            )
            .order_by(Tenant.id)
            .all()
        )
