"""監査ログのビジネスロジックを提供するモジュール。"""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import (
    client_ip_context,
    request_id_context,
    user_agent_context,
)
from app.repositories.audit_log import AuditLogRepository

logger = logging.getLogger(__name__)

SENSITIVE_METADATA_KEYS = {
    "access_token",
    "authorization",
    "cookie",
    "csrf",
    "csrf_token",
    "hash",
    "hashed_password",
    "password",
    "secret",
    "signature",
    "token",
}


class AuditEventType:
    """監査ログのイベント種別定数。"""

    AUTH_LOGIN_SUCCESS = "auth.login.success"
    AUTH_LOGIN_FAILURE = "auth.login.failure"
    AUTH_LOGOUT = "auth.logout"
    AUTH_SESSION_REVOKED = "auth.session.revoked"
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_DELETED = "user.deleted"
    USER_SYSTEM_ROLES_CHANGED = "user.system_roles.changed"
    PROJECT_CREATED = "project.created"
    PROJECT_UPDATED = "project.updated"
    PROJECT_DELETED = "project.deleted"
    PROJECT_MEMBER_ADDED = "project_member.added"
    PROJECT_MEMBER_ROLE_CHANGED = "project_member.role_changed"
    PROJECT_MEMBER_REMOVED = "project_member.removed"


class AuditLogService:
    """重要操作の監査ログを記録する。"""

    def __init__(self, repository: AuditLogRepository | None = None) -> None:
        """AuditLogServiceを初期化する。

        Args:
            repository: 監査ログRepository。
        """
        self.repository = repository or AuditLogRepository()

    def record(
        self,
        db: Session,
        *,
        event_type: str,
        actor_user_id: int | None = None,
        target_user_id: int | None = None,
        project_id: int | None = None,
        resource_type: str | None = None,
        resource_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """監査ログを記録する。

        監査ログ記録に失敗しても本来の業務処理は失敗させない。

        Args:
            db: DBセッション。
            event_type: イベント種別。
            actor_user_id: 操作ユーザーID。
            target_user_id: 対象ユーザーID。
            project_id: 関連プロジェクトID。
            resource_type: リソース種別。
            resource_id: リソースID。
            metadata: 追加メタデータ。
        """
        try:
            self.repository.create(
                db,
                event_type=event_type,
                actor_user_id=actor_user_id,
                target_user_id=target_user_id,
                project_id=project_id,
                resource_type=resource_type,
                resource_id=resource_id,
                ip_address=client_ip_context.get(),
                user_agent=user_agent_context.get(),
                request_id=request_id_context.get(),
                metadata=self._sanitize_metadata(metadata or {}),
            )
        except Exception:
            db.rollback()
            logger.exception("Failed to record audit log: event_type=%s", event_type)

    def count_cleanup_targets(self, db: Session, *, older_than_days: int) -> int:
        """削除対象の監査ログ件数を取得する。

        Args:
            db: DBセッション。
            older_than_days: 削除対象とする経過日数。

        Returns:
            削除対象の監査ログ件数。
        """
        return self.repository.count_older_than(
            db,
            older_than_days=older_than_days,
        )

    def cleanup_old_logs(
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
        return self.repository.delete_older_than(
            db,
            older_than_days=older_than_days,
            limit=limit,
        )

    def _sanitize_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """監査ログのmetadataから秘匿情報を除外する。

        Args:
            metadata: 監査ログに保存するmetadata。

        Returns:
            秘匿情報を除外したmetadata。
        """
        sanitized: dict[str, Any] = {}
        for key, value in metadata.items():
            if self._is_sensitive_key(key):
                continue

            if isinstance(value, dict):
                sanitized[key] = self._sanitize_metadata(value)
            elif isinstance(value, list):
                sanitized[key] = [
                    self._sanitize_metadata(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                sanitized[key] = value

        return sanitized

    def _is_sensitive_key(self, key: str) -> bool:
        """metadata keyが秘匿情報を示すか判定する。

        Args:
            key: metadataのkey。

        Returns:
            秘匿情報を示すkeyの場合はTrue。
        """
        normalized_key = key.lower()
        return any(sensitive in normalized_key for sensitive in SENSITIVE_METADATA_KEYS)
