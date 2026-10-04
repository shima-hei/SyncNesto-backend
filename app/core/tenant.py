"""認証済みIdentityから現在の組織を確定するDependency。"""

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.auth import get_current_user
from app.core.exceptions import BadRequestError, ForbiddenError
from app.db.session import get_db
from app.models.project import Project
from app.models.rbac import Role
from app.models.tenant import Tenant, TenantMember
from app.models.user import User
from app.services.authorization import AuthorizationService


def get_current_tenant(
    tenant_id: int | None = Header(default=None, alias="X-Tenant-ID"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Tenant:
    """選択IDを所属と組織状態で検証し、DB Contextを固定する。"""
    query = (
        db.query(Tenant, TenantMember, Role)
        .join(TenantMember, TenantMember.tenant_id == Tenant.id)
        .join(Role, Role.id == TenantMember.role_id)
        .filter(
            TenantMember.user_id == user.id,
            TenantMember.status == "active",
            Tenant.status == "active",
            Role.scope == "tenant",
        )
    )
    if tenant_id is not None:
        query = query.filter(Tenant.id == tenant_id)
    choices = query.limit(2).all()
    if not choices:
        raise ForbiddenError(error_messages.TENANT_ACCESS_DENIED)
    if len(choices) != 1:
        raise BadRequestError(error_messages.TENANT_SELECTION_REQUIRED)
    tenant, membership, role = choices[0]
    db.info.update(
        tenant_id=tenant.id,
        tenant_user_id=user.id,
        tenant_role=role.key,
        tenant_member_id=membership.id,
    )
    return tenant


def require_tenant_admin(
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """現在の組織内の管理者・Ownerを要求する。"""
    if db.info.get("tenant_role") not in {"tenant_owner", "tenant_admin"}:
        raise ForbiddenError()
    return user


def require_project_management(permission: str):
    """組織管理者またはProject権限を持つ本人に、管理メタデータ操作を許可する。"""

    def dependency(
        project_id: int,
        tenant: Tenant = Depends(get_current_tenant),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        """管理対象の所属を確認する。業務内容の認可には使わない。"""
        project = (
            db.query(Project)
            .filter(
                Project.id == project_id,
                Project.tenant_id == tenant.id,
                Project.deleted_at.is_(None),
            )
            .first()
        )
        if project is None:
            raise ForbiddenError()
        if db.info.get("tenant_role") in {"tenant_owner", "tenant_admin"}:
            return user
        if not AuthorizationService().has_project_permission(
            db, user=user, project_id=project_id, permission_code=permission
        ):
            raise ForbiddenError()
        return user

    return dependency
