"""システム運営用の参照とレスポンス整形。業務権限は追加しない。"""

from collections import defaultdict
from typing import cast

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.tenant import Tenant
from app.repositories.system_management import SystemManagementRepository
from app.schemas.system_management import (
    SystemSummary,
    SystemTenantListResponse,
    SystemTenantMemberListResponse,
    SystemTenantMemberRead,
    SystemTenantOwner,
    SystemTenantRead,
    SystemUserMembershipRead,
)
from app.schemas.tenant import TenantRead, TenantRoleKey


class SystemManagementService:
    """組織と共通Identityの運営情報を読み取る。"""

    def __init__(self) -> None:
        """読み取り専用Repositoryを用意する。"""
        self.repository = SystemManagementRepository()

    def summary(self, db: Session) -> SystemSummary:
        """管理ホームの件数を返す。"""
        return SystemSummary(**self.repository.summary(db))

    def _tenants(self, db: Session, tenants: list[Tenant]) -> list[SystemTenantRead]:
        """取得済み組織へ人数とOwnerを一括で付ける。"""
        counts, owner_rows = self.repository.tenant_metadata(
            db, [t.id for t in tenants]
        )
        count_map = {tenant_id: (total, active) for tenant_id, total, active in counts}
        owners: dict[int, list[SystemTenantOwner]] = defaultdict(list)
        for member, user in owner_rows:
            owners[member.tenant_id].append(
                SystemTenantOwner(
                    user_id=user.id,
                    display_name=member.display_name or user.name,
                    email=user.email,
                )
            )
        return [
            SystemTenantRead(
                **TenantRead.model_validate(tenant).model_dump(),
                member_count=count_map.get(tenant.id, (0, 0))[0],
                active_member_count=count_map.get(tenant.id, (0, 0))[1],
                owners=owners[tenant.id],
            )
            for tenant in tenants
        ]

    def tenants(
        self,
        db: Session,
        *,
        page: int,
        page_size: int,
        q: str | None,
        status: str | None,
    ) -> SystemTenantListResponse:
        """運営対象の組織を検索する。"""
        tenants, total = self.repository.tenants(
            db, page=page, page_size=page_size, q=q, status=status
        )
        return SystemTenantListResponse(
            items=self._tenants(db, tenants),
            total=total,
            page=page,
            page_size=page_size,
        )

    def tenant(self, db: Session, tenant_id: int) -> SystemTenantRead:
        """存在する組織の管理情報を返す。"""
        tenant = self.repository.tenant(db, tenant_id)
        if tenant is None:
            raise NotFoundError()
        return self._tenants(db, [tenant])[0]

    def members(
        self,
        db: Session,
        tenant_id: int,
        *,
        page: int,
        page_size: int,
        q: str | None,
        status: str | None,
    ) -> SystemTenantMemberListResponse:
        """組織内プロフィールとアカウント状態を別々に返す。"""
        if self.repository.tenant(db, tenant_id) is None:
            raise NotFoundError()
        rows, total = self.repository.members(
            db, tenant_id, page=page, page_size=page_size, q=q, status=status
        )
        return SystemTenantMemberListResponse(
            items=[
                SystemTenantMemberRead(
                    id=member.id,
                    user_id=user.id,
                    email=user.email,
                    display_name=member.display_name or user.name,
                    department=member.department,
                    position=member.position,
                    role_key=cast(TenantRoleKey, role.key),
                    status=member.status,
                    version=member.version,
                    joined_at=member.joined_at,
                    account_is_active=user.is_active,
                    last_login_at=user.last_login_at,
                )
                for member, user, role in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    def memberships(self, db: Session, user_id: int) -> list[SystemUserMembershipRead]:
        """ユーザー詳細から全組織の所属を確認する。"""
        user = self.repository.user(db, user_id)
        if user is None:
            raise NotFoundError()
        return [
            SystemUserMembershipRead(
                tenant_id=tenant.id,
                tenant_name=tenant.name,
                tenant_slug=tenant.slug,
                tenant_status=tenant.status,
                role_key=cast(TenantRoleKey, role.key),
                status=member.status,
                display_name=member.display_name or user.name,
                department=member.department,
                position=member.position,
            )
            for tenant, member, role in self.repository.memberships(db, user_id)
        ]
