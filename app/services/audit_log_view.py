"""監査ログ閲覧の認可と公開情報の限定。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import BadRequestError, ForbiddenError
from app.repositories.audit_log import AuditLogRepository
from app.schemas.audit_log import AuditLogListRead, AuditLogRead

FIELDS = frozenset(
    {
        "title",
        "body",
        "description",
        "status",
        "priority",
        "assignee_id",
        "start_date",
        "due_date",
        "progress",
        "name",
        "display_name",
        "department",
        "position",
        "role_key",
        "requirement_type",
        "parent_task_id",
        "section_id",
        "sort_order",
        "tag",
        "tags",
        "version",
        "deleted_at",
    }
)
ROLES = frozenset(
    {
        "tenant_owner",
        "tenant_admin",
        "tenant_member",
        "system_admin",
        "project_admin",
        "manager",
        "member",
        "viewer",
    }
)


def safe_details(
    metadata: dict[str, Any],
) -> dict[str, str | int | list[str] | list[int]]:
    """既知の項目名・ロール・数値だけを公開し、自由記述は返さない。"""
    result: dict[str, str | int | list[str] | list[int]] = {}
    resource_id = metadata.get("id")
    if isinstance(resource_id, str):
        try:
            result["id"] = str(UUID(resource_id))
        except ValueError:
            pass
    fields = metadata.get("updated_fields")
    if isinstance(fields, list):
        result["updated_fields"] = sorted(
            {value for value in fields if isinstance(value, str) and value in FIELDS}
        )
    for key in ("role_key", "before_role_key", "after_role_key"):
        value = metadata.get(key)
        if isinstance(value, str) and value in ROLES:
            result[key] = value
    for key in (
        "version",
        "restored_version",
        "retention_days",
        "count",
        "purged_count",
        "failed_count",
    ):
        value = metadata.get(key)
        if type(value) is int and 0 <= value <= 2**31 - 1:
            result[key] = value
    mode = metadata.get("mode")
    if isinstance(mode, str) and mode in {"dry_run", "execute"}:
        result["mode"] = mode
    return result


class AuditLogViewService:
    """現在の組織の所有者・管理者に監査記録を提供する。"""

    def list(
        self,
        db: Session,
        *,
        tenant_id: int,
        event_type: str | None,
        actor_user_id: int | None,
        project_id: int | None,
        created_from: datetime | None,
        created_before: datetime | None,
        page: int,
        page_size: int,
    ) -> AuditLogListRead:
        """所属・管理権限と期間を確認し、安全なschemaへ変換する。"""
        if db.info.get("tenant_id") != tenant_id or db.info.get("tenant_role") not in {
            "tenant_owner",
            "tenant_admin",
        }:
            raise ForbiddenError()
        if created_from and created_before and created_from >= created_before:
            raise BadRequestError(error_messages.AUDIT_LOG_DATE_RANGE_INVALID)
        rows, total = AuditLogRepository().list_for_tenant(
            db,
            tenant_id=tenant_id,
            event_type=event_type,
            actor_user_id=actor_user_id,
            project_id=project_id,
            created_from=created_from,
            created_before=created_before,
            page=page,
            page_size=page_size,
        )
        return AuditLogListRead(
            items=[
                AuditLogRead(
                    id=row.id,
                    created_at=row.created_at,
                    event_type=row.event_type,
                    actor_user_id=row.actor_user_id,
                    actor_name=actor_name,
                    project_id=row.project_id,
                    project_name=project_name,
                    resource_type=row.resource_type,
                    resource_id=row.resource_id,
                    source="mcp"
                    if row.extra_metadata.get("source") == "mcp"
                    else "app",
                    details=safe_details(row.extra_metadata),
                )
                for row, actor_name, project_name in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
        )
