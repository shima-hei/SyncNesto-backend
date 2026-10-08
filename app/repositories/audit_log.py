"""監査ログRepositoryを定義するモジュール。"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.project import Project
from app.models.tenant import TenantMember


class AuditLogRepository:
    """AuditLogテーブルへのデータアクセス処理を提供するRepository。"""

    def list_for_tenant(
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
    ) -> tuple[list[tuple[AuditLog, str | None, str | None]], int]:
        """所属の名称を同じ組織に限定し、削除済み対象の証跡も取得する。"""
        query = db.query(AuditLog).filter(AuditLog.tenant_id == tenant_id)
        if event_type is not None:
            query = query.filter(AuditLog.event_type == event_type)
        if actor_user_id is not None:
            query = query.filter(AuditLog.actor_user_id == actor_user_id)
        if project_id is not None:
            query = query.filter(AuditLog.project_id == project_id)
        if created_from is not None:
            query = query.filter(AuditLog.created_at >= created_from)
        if created_before is not None:
            query = query.filter(AuditLog.created_at < created_before)
        total = query.count()
        rows = (
            query.add_columns(TenantMember.display_name, Project.name)
            .outerjoin(
                TenantMember,
                and_(
                    TenantMember.tenant_id == tenant_id,
                    TenantMember.user_id == AuditLog.actor_user_id,
                ),
            )
            .outerjoin(
                Project,
                and_(Project.tenant_id == tenant_id, Project.id == AuditLog.project_id),
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return [(row[0], row[1], row[2]) for row in rows], total

    def create(
        self,
        db: Session,
        *,
        event_type: str,
        tenant_id: int | None = None,
        actor_user_id: int | None = None,
        target_user_id: int | None = None,
        project_id: int | None = None,
        resource_type: str | None = None,
        resource_id: int | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        """監査ログを作成する。

        Args:
            db: DBセッション。
            event_type: イベント種別。
            actor_user_id: 操作ユーザーID。
            target_user_id: 対象ユーザーID。
            project_id: 関連プロジェクトID。
            resource_type: リソース種別。
            resource_id: リソースID。
            ip_address: 接続元IPアドレス。
            user_agent: User-Agent。
            request_id: リクエストID。
            metadata: 追加メタデータ。

        Returns:
            作成された監査ログ。
        """
        audit_log = AuditLog(
            tenant_id=tenant_id if tenant_id is not None else db.info.get("tenant_id"),
            event_type=event_type,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            project_id=project_id,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
            extra_metadata=metadata or {},
        )
        db.add(audit_log)
        db.commit()
        db.refresh(audit_log)
        return audit_log

    def count_older_than(self, db: Session, *, older_than_days: int) -> int:
        """指定日数より古い監査ログ件数を取得する。

        Args:
            db: DBセッション。
            older_than_days: 削除対象とする経過日数。

        Returns:
            対象監査ログ件数。
        """
        threshold = datetime.now(UTC) - timedelta(days=older_than_days)
        return db.query(AuditLog).filter(AuditLog.created_at < threshold).count()

    def delete_older_than(
        self,
        db: Session,
        *,
        older_than_days: int,
        limit: int | None = None,
    ) -> int:
        """指定日数より古い監査ログを削除する。

        Args:
            db: DBセッション。
            older_than_days: 削除対象とする経過日数。
            limit: 最大削除件数。

        Returns:
            削除した監査ログ件数。
        """
        threshold = datetime.now(UTC) - timedelta(days=older_than_days)
        query = (
            db.query(AuditLog.id)
            .filter(AuditLog.created_at < threshold)
            .order_by(AuditLog.created_at)
        )
        if limit is not None:
            query = query.limit(limit)

        audit_log_ids = [row.id for row in query.all()]
        if not audit_log_ids:
            return 0

        deleted_count = (
            db.query(AuditLog).filter(AuditLog.id.in_(audit_log_ids)).delete()
        )
        db.commit()
        return deleted_count
