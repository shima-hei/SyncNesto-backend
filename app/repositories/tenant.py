"""組織と所属のDBアクセス。変更はServiceのトランザクションにまとめる。"""

from sqlalchemy.orm import Session

from app.models.rbac import Role
from app.models.tenant import Tenant, TenantMember
from app.models.user import User


class TenantRepository:
    """Tenant境界を明示した所属操作。"""

    def choices(self, db: Session, user_id: int):
        """本人の有効な所属だけを取得する。"""
        return (
            db.query(Tenant, Role)
            .join(TenantMember, TenantMember.tenant_id == Tenant.id)
            .join(Role, Role.id == TenantMember.role_id)
            .filter(
                TenantMember.user_id == user_id,
                TenantMember.status == "active",
                Tenant.status == "active",
                Role.scope == "tenant",
            )
            .order_by(Tenant.id)
            .all()
        )

    def list_tenants(self, db: Session) -> list[Tenant]:
        """運営者向けに組織メタデータを取得する。"""
        return db.query(Tenant).order_by(Tenant.id).all()

    def lock(self, db: Session, tenant_id: int) -> Tenant | None:
        """Owner変更を組織単位で直列化する。"""
        return (
            db.query(Tenant)
            .filter(Tenant.id == tenant_id)
            .with_for_update()
            .populate_existing()
            .first()
        )

    def members(self, db: Session, tenant_id: int):
        """現在の組織内のIdentityとプロフィールだけを取得する。"""
        return (
            db.query(TenantMember, User, Role)
            .join(User, User.id == TenantMember.user_id)
            .join(Role, Role.id == TenantMember.role_id)
            .filter(
                TenantMember.tenant_id == tenant_id, TenantMember.status != "removed"
            )
            .order_by(TenantMember.id)
            .all()
        )

    def member(self, db: Session, tenant_id: int, user_id: int) -> TenantMember | None:
        """組織とIdentityの完全一致で所属を取得する。"""
        return (
            db.query(TenantMember)
            .filter(
                TenantMember.tenant_id == tenant_id, TenantMember.user_id == user_id
            )
            .populate_existing()
            .first()
        )

    def identity(self, db: Session, email: str) -> User | None:
        """有効なIdentityをメール完全一致で探す。"""
        return (
            db.query(User)
            .filter(
                User.email == email, User.deleted_at.is_(None), User.is_active.is_(True)
            )
            .first()
        )

    def role(self, db: Session, key: str) -> Role | None:
        """安定したkeyとscopeで組織Roleを取得する。"""
        return db.query(Role).filter(Role.key == key, Role.scope == "tenant").first()

    def owner_count(self, db: Session, tenant_id: int) -> int:
        """有効なOwnerを数える。"""
        return (
            db.query(TenantMember)
            .join(Role, Role.id == TenantMember.role_id)
            .join(User, User.id == TenantMember.user_id)
            .filter(
                TenantMember.tenant_id == tenant_id,
                TenantMember.status == "active",
                Role.key == "tenant_owner",
                Role.scope == "tenant",
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .count()
        )
