"""監査ログRepositoryを定義するモジュール。"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


class AuditLogRepository:
    """AuditLogテーブルへのデータアクセス処理を提供するRepository。"""

    def create(
        self,
        db: Session,
        *,
        event_type: str,
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
