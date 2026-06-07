"""監査ログのビジネスロジックを提供するモジュール。"""

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.core.logging import (
    client_ip_context,
    request_id_context,
    user_agent_context,
)
from app.repositories.audit_log import AuditLogRepository

if TYPE_CHECKING:
    from app.models.project import Project, ProjectMember
    from app.models.session import UserSession
    from app.models.user import User

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

    def record_login_success(
        self,
        db: Session,
        *,
        user: "User",
        user_session: "UserSession",
    ) -> None:
        """ログイン成功の監査ログを記録する。

        Args:
            db: DBセッション。
            user: ログインしたユーザー。
            user_session: 作成された認証セッション。
        """
        self.record(
            db,
            event_type=AuditEventType.AUTH_LOGIN_SUCCESS,
            actor_user_id=user.id,
            target_user_id=user.id,
            resource_type="session",
            metadata={"session_id": str(user_session.id), "email": user.email},
        )

    def record_login_failure(
        self,
        db: Session,
        *,
        email: str,
        reason: str,
        user: "User | None" = None,
    ) -> None:
        """ログイン失敗の監査ログを記録する。

        Args:
            db: DBセッション。
            email: ログイン試行に使われたemail。
            reason: 失敗理由。
            user: 対象ユーザー。存在しないemailの場合はNone。
        """
        self.record(
            db,
            event_type=AuditEventType.AUTH_LOGIN_FAILURE,
            target_user_id=user.id if user is not None else None,
            resource_type="user" if user is not None else None,
            resource_id=user.id if user is not None else None,
            metadata={"email": email, "reason": reason},
        )

    def record_logout(
        self,
        db: Session,
        *,
        user_session: "UserSession | None",
    ) -> None:
        """ログアウトの監査ログを記録する。

        Args:
            db: DBセッション。
            user_session: 失効した認証セッション。取得できない場合はNone。
        """
        user_id = user_session.user_id if user_session is not None else None
        self.record(
            db,
            event_type=AuditEventType.AUTH_LOGOUT,
            actor_user_id=user_id,
            target_user_id=user_id,
            resource_type="session",
            metadata={
                "session_id": str(user_session.id)
                if user_session is not None
                else None,
            },
        )

    def record_session_revoked(
        self,
        db: Session,
        *,
        actor_user_id: int | None,
        target_user_id: int,
        project_id: int | None,
        reason: str,
        revoked_count: int,
    ) -> None:
        """セッション失効の監査ログを記録する。

        Args:
            db: DBセッション。
            actor_user_id: 操作ユーザーID。
            target_user_id: セッション失効対象ユーザーID。
            project_id: 関連プロジェクトID。
            reason: 失効理由。
            revoked_count: 失効したセッション件数。
        """
        self.record(
            db,
            event_type=AuditEventType.AUTH_SESSION_REVOKED,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            project_id=project_id,
            resource_type="session",
            metadata={
                "reason": reason,
                "revoked_count": revoked_count,
            },
        )

    def record_user_created(
        self,
        db: Session,
        *,
        actor_user_id: int | None,
        user: "User",
    ) -> None:
        """ユーザー作成の監査ログを記録する。

        Args:
            db: DBセッション。
            actor_user_id: 操作ユーザーID。
            user: 作成されたユーザー。
        """
        self.record(
            db,
            event_type=AuditEventType.USER_CREATED,
            actor_user_id=actor_user_id,
            target_user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            metadata={"email": user.email},
        )

    def record_user_updated(
        self,
        db: Session,
        *,
        actor_user_id: int | None,
        user: "User",
        updated_fields: Iterable[str],
    ) -> None:
        """ユーザー更新の監査ログを記録する。

        Args:
            db: DBセッション。
            actor_user_id: 操作ユーザーID。
            user: 更新されたユーザー。
            updated_fields: 更新された入力フィールド名。
        """
        self.record(
            db,
            event_type=AuditEventType.USER_UPDATED,
            actor_user_id=actor_user_id,
            target_user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            metadata={"updated_fields": sorted(updated_fields)},
        )

    def record_user_deleted(
        self,
        db: Session,
        *,
        actor_user_id: int | None,
        user: "User",
    ) -> None:
        """ユーザー削除の監査ログを記録する。

        Args:
            db: DBセッション。
            actor_user_id: 操作ユーザーID。
            user: 削除されたユーザー。
        """
        self.record(
            db,
            event_type=AuditEventType.USER_DELETED,
            actor_user_id=actor_user_id,
            target_user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            metadata={"email": user.email},
        )

    def record_user_system_roles_changed(
        self,
        db: Session,
        *,
        actor_user_id: int | None,
        user: "User",
        before_role_keys: list[str],
        after_role_keys: list[str],
    ) -> None:
        """ユーザーのシステムロール変更の監査ログを記録する。

        Args:
            db: DBセッション。
            actor_user_id: 操作ユーザーID。
            user: ロール変更対象ユーザー。
            before_role_keys: 変更前ロールkey一覧。
            after_role_keys: 変更後ロールkey一覧。
        """
        self.record(
            db,
            event_type=AuditEventType.USER_SYSTEM_ROLES_CHANGED,
            actor_user_id=actor_user_id,
            target_user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            metadata={
                "before_role_keys": before_role_keys,
                "after_role_keys": after_role_keys,
            },
        )

    def record_project_created(
        self,
        db: Session,
        *,
        actor_user_id: int | None,
        project: "Project",
    ) -> None:
        """プロジェクト作成の監査ログを記録する。

        Args:
            db: DBセッション。
            actor_user_id: 操作ユーザーID。
            project: 作成されたプロジェクト。
        """
        self.record(
            db,
            event_type=AuditEventType.PROJECT_CREATED,
            actor_user_id=actor_user_id,
            project_id=project.id,
            resource_type="project",
            resource_id=project.id,
            metadata={"project_code": project.project_code, "name": project.name},
        )

    def record_project_updated(
        self,
        db: Session,
        *,
        actor_user_id: int | None,
        project: "Project",
        updated_fields: Iterable[str],
    ) -> None:
        """プロジェクト更新の監査ログを記録する。

        Args:
            db: DBセッション。
            actor_user_id: 操作ユーザーID。
            project: 更新されたプロジェクト。
            updated_fields: 更新された入力フィールド名。
        """
        self.record(
            db,
            event_type=AuditEventType.PROJECT_UPDATED,
            actor_user_id=actor_user_id,
            project_id=project.id,
            resource_type="project",
            resource_id=project.id,
            metadata={"updated_fields": sorted(updated_fields)},
        )

    def record_project_deleted(
        self,
        db: Session,
        *,
        actor_user_id: int | None,
        project: "Project",
    ) -> None:
        """プロジェクト削除の監査ログを記録する。

        Args:
            db: DBセッション。
            actor_user_id: 操作ユーザーID。
            project: 削除されたプロジェクト。
        """
        self.record(
            db,
            event_type=AuditEventType.PROJECT_DELETED,
            actor_user_id=actor_user_id,
            project_id=project.id,
            resource_type="project",
            resource_id=project.id,
            metadata={"project_code": project.project_code, "name": project.name},
        )

    def record_project_member_added(
        self,
        db: Session,
        *,
        actor_user_id: int | None,
        member: "ProjectMember",
        role_key: str,
    ) -> None:
        """プロジェクトメンバー追加の監査ログを記録する。

        Args:
            db: DBセッション。
            actor_user_id: 操作ユーザーID。
            member: 追加されたプロジェクトメンバー。
            role_key: 付与したプロジェクトロールkey。
        """
        self.record(
            db,
            event_type=AuditEventType.PROJECT_MEMBER_ADDED,
            actor_user_id=actor_user_id,
            target_user_id=member.user_id,
            project_id=member.project_id,
            resource_type="project_member",
            resource_id=member.id,
            metadata={"role_key": role_key},
        )

    def record_project_member_role_changed(
        self,
        db: Session,
        *,
        actor_user_id: int | None,
        member: "ProjectMember",
        before_role_key: str,
        after_role_key: str,
    ) -> None:
        """プロジェクトメンバーロール変更の監査ログを記録する。

        Args:
            db: DBセッション。
            actor_user_id: 操作ユーザーID。
            member: 更新されたプロジェクトメンバー。
            before_role_key: 変更前プロジェクトロールkey。
            after_role_key: 変更後プロジェクトロールkey。
        """
        self.record(
            db,
            event_type=AuditEventType.PROJECT_MEMBER_ROLE_CHANGED,
            actor_user_id=actor_user_id,
            target_user_id=member.user_id,
            project_id=member.project_id,
            resource_type="project_member",
            resource_id=member.id,
            metadata={
                "before_role_key": before_role_key,
                "after_role_key": after_role_key,
            },
        )

    def record_project_member_removed(
        self,
        db: Session,
        *,
        actor_user_id: int | None,
        project_id: int,
        user_id: int,
        member_id: int,
        role_key: str,
    ) -> None:
        """プロジェクトメンバー削除の監査ログを記録する。

        Args:
            db: DBセッション。
            actor_user_id: 操作ユーザーID。
            project_id: プロジェクトID。
            user_id: 削除対象ユーザーID。
            member_id: 削除されたプロジェクトメンバーID。
            role_key: 削除前プロジェクトロールkey。
        """
        self.record(
            db,
            event_type=AuditEventType.PROJECT_MEMBER_REMOVED,
            actor_user_id=actor_user_id,
            target_user_id=user_id,
            project_id=project_id,
            resource_type="project_member",
            resource_id=member_id,
            metadata={"role_key": role_key},
        )

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
