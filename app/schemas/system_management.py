"""システム運営用の組織・Identity参照契約。"""

from datetime import datetime

from pydantic import BaseModel

from app.schemas.tenant import TenantMemberRead, TenantRead, TenantRoleKey


class SystemSummary(BaseModel):
    """通常の運営対象を重複なく集計した件数。"""

    total_tenants: int
    active_tenants: int
    suspended_tenants: int
    total_users: int
    active_users: int
    inactive_users: int
    users_without_active_membership: int


class SystemTenantOwner(BaseModel):
    """組織の有効なOwner。"""

    user_id: int
    display_name: str
    email: str


class SystemTenantRead(TenantRead):
    """所属人数とOwnerを含む運営用組織メタデータ。"""

    member_count: int
    active_member_count: int
    owners: list[SystemTenantOwner]


class SystemTenantListResponse(BaseModel):
    """検索条件に対する組織一覧と総数。"""

    items: list[SystemTenantRead]
    total: int
    page: int
    page_size: int


class SystemTenantMemberRead(TenantMemberRead):
    """組織所属とアカウント全体の状態を区別したユーザー情報。"""

    account_is_active: bool
    last_login_at: datetime | None


class SystemTenantMemberListResponse(BaseModel):
    """指定組織の所属ユーザー一覧と総数。"""

    items: list[SystemTenantMemberRead]
    total: int
    page: int
    page_size: int


class SystemUserMembershipRead(BaseModel):
    """ユーザーの組織別所属。削除済み所属は含めない。"""

    tenant_id: int
    tenant_name: str
    tenant_slug: str
    tenant_status: str
    role_key: TenantRoleKey
    status: str
    display_name: str
    department: str | None
    position: str | None
