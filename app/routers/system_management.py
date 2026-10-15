"""組織所属に依存しない運営用の読み取りAPI。"""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import require_system_permission
from app.db.session import get_db
from app.schemas.system_management import (
    SystemSummary,
    SystemTenantListResponse,
    SystemTenantMemberListResponse,
    SystemTenantRead,
    SystemUserMembershipRead,
)
from app.services.system_management import SystemManagementService

router = APIRouter(
    prefix="/system",
    tags=["system-management"],
    dependencies=[
        Depends(require_system_permission("tenant:manage")),
        Depends(require_system_permission("user:read")),
    ],
)
service = SystemManagementService()


@router.get("/summary", response_model=SystemSummary)
def get_system_summary(db: Session = Depends(get_db)) -> SystemSummary:
    """管理ホームの組織数・ユーザー数を返す。"""
    return service.summary(db)


@router.get("/tenants", response_model=SystemTenantListResponse)
def list_system_tenants(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str | None = Query(default=None, max_length=255),
    status: Literal["active", "suspended"] | None = None,
    db: Session = Depends(get_db),
) -> SystemTenantListResponse:
    """全組織のメタデータを検索・ページ分割する。"""
    return service.tenants(db, page=page, page_size=page_size, q=q, status=status)


@router.get("/tenants/{tenant_id}", response_model=SystemTenantRead)
def get_system_tenant(
    tenant_id: int, db: Session = Depends(get_db)
) -> SystemTenantRead:
    """指定組織の管理情報を取得する。"""
    return service.tenant(db, tenant_id)


@router.get(
    "/tenants/{tenant_id}/members", response_model=SystemTenantMemberListResponse
)
def list_system_tenant_members(
    tenant_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str | None = Query(default=None, max_length=255),
    status: Literal["active", "suspended"] | None = None,
    db: Session = Depends(get_db),
) -> SystemTenantMemberListResponse:
    """本人の所属に関係なく、指定組織の所属ユーザーを確認する。"""
    return service.members(
        db, tenant_id, page=page, page_size=page_size, q=q, status=status
    )


@router.get(
    "/users/{user_id}/memberships", response_model=list[SystemUserMembershipRead]
)
def list_system_user_memberships(
    user_id: int, db: Session = Depends(get_db)
) -> list[SystemUserMembershipRead]:
    """指定ユーザーの複数組織への所属を取得する。"""
    return service.memberships(db, user_id)
