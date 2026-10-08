"""現在の組織の監査ログ閲覧API。"""

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime
from sqlalchemy.orm import Session

from app.core.tenant import get_current_tenant, require_tenant_admin
from app.db.session import get_db
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.audit_log import AuditLogListRead
from app.services.audit_log_view import AuditLogViewService

router = APIRouter(prefix="/tenants/current/audit-logs", tags=["audit-logs"])


@router.get("", response_model=AuditLogListRead)
def list_audit_logs(
    event_type: str | None = Query(default=None, min_length=1, max_length=100),
    actor_user_id: int | None = Query(default=None, ge=1, le=2**31 - 1),
    project_id: int | None = Query(default=None, ge=1, le=2**31 - 1),
    created_from: AwareDatetime | None = None,
    created_before: AwareDatetime | None = None,
    page: int = Query(default=1, ge=1, le=500),
    page_size: int = Query(default=25, ge=1, le=50),
    tenant: Tenant = Depends(get_current_tenant),
    _: User = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
) -> AuditLogListRead:
    """組織管理者が監査記録を期間・操作者・操作・案件で検索する。"""
    return AuditLogViewService().list(
        db,
        tenant_id=tenant.id,
        event_type=event_type,
        actor_user_id=actor_user_id,
        project_id=project_id,
        created_from=created_from,
        created_before=created_before,
        page=page,
        page_size=page_size,
    )
