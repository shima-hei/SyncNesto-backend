"""要件定義変更履歴サービスを定義するモジュール。"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import NotFoundError
from app.models.requirement import RequirementChangeLog
from app.repositories.requirement import (
    RequirementChangeLogRepository,
    RequirementDocumentRepository,
)
from app.repositories.user import UserRepository
from app.schemas.change_log import ChangeLogUserRead
from app.schemas.requirement import RequirementChangeLogRead
from app.services.change_log_formatter import (
    ChangeLogFormatConfig,
    ChangeLogFormatter,
)

REQUIREMENT_CHANGE_LOG_ACTION_MAP = {
    "comment.created": "comment_created",
    "comment.updated": "comment_updated",
    "comment.deleted": "comment_deleted",
    "comment.resolved": "comment_resolved",
    "comment.reopened": "comment_reopened",
    "approval.requested": "approval_requested",
    "approval.approved": "approval_approved",
    "approval.rejected": "approval_rejected",
}
REQUIREMENT_CHANGE_LOG_TARGET_TYPE_MAP = {
    "comment": "requirement_comment",
    "detail": "requirement_detail",
    "document": "requirement_document",
    "link": "requirement_link",
    "open_issue": "requirement_open_issue",
    "relation": "requirement_relation",
    "requirement_item": "requirement",
    "review": "requirement_review",
    "section": "requirement_section",
}
REQUIREMENT_STATUS_LABELS = {
    "draft": "下書き",
    "review": "レビュー中",
    "approved": "承認済み",
    "rejected": "差し戻し",
    "open": "未解決",
    "resolved": "解決済み",
    "closed": "クローズ",
    "requested": "申請中",
    "pending": "保留中",
}
REQUIREMENT_PRIORITY_LABELS = {
    "must": "Must",
    "should": "Should",
    "could": "Could",
    "wont": "Won't",
}
REQUIREMENT_TYPE_LABELS = {
    "functional": "機能要件",
    "non_functional": "非機能要件",
    "business": "業務要件",
    "system": "システム要件",
    "constraint": "制約",
}
REQUIREMENT_CHANGE_LOG_FORMATTER = ChangeLogFormatter(
    ChangeLogFormatConfig(
        action_map=REQUIREMENT_CHANGE_LOG_ACTION_MAP,
        target_type_map=REQUIREMENT_CHANGE_LOG_TARGET_TYPE_MAP,
        field_names={
            "title",
            "document_code",
            "status",
            "purpose",
            "author_id",
            "reviewer_id",
            "approver_id",
            "sort_order",
            "requirement_code",
            "requirement_type",
            "category",
            "description",
            "rationale",
            "acceptance_criteria",
            "priority",
            "source",
            "owner_id",
            "issue_code",
            "assignee_id",
            "due_date",
            "body",
            "is_resolved",
        },
        field_value_labels={
            "status": REQUIREMENT_STATUS_LABELS,
            "priority": REQUIREMENT_PRIORITY_LABELS,
            "requirement_type": REQUIREMENT_TYPE_LABELS,
        },
        user_id_fields={
            "author_id",
            "reviewer_id",
            "approver_id",
            "owner_id",
            "assignee_id",
        },
    )
)


class RequirementChangeLogAction:
    """要件定義変更履歴の操作種別定数。"""

    COMMENT_CREATED = "comment.created"
    COMMENT_DELETED = "comment.deleted"
    COMMENT_REOPENED = "comment.reopened"
    COMMENT_RESOLVED = "comment.resolved"
    COMMENT_UPDATED = "comment.updated"
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    EXPORTED = "exported"
    PROMOTED_TO_REQUIREMENT = "promoted_to_requirement"
    SORTED = "sorted"


class RequirementChangeLogTargetType:
    """要件定義変更履歴の対象種別定数。"""

    COMMENT = "comment"
    DETAIL = "detail"
    DOCUMENT = "document"
    LINK = "link"
    OPEN_ISSUE = "open_issue"
    RELATION = "relation"
    REQUIREMENT_ITEM = "requirement_item"
    REVIEW = "review"
    SECTION = "section"


class RequirementChangeLogService:
    """要件定義の横断変更履歴に関するビジネスロジックを提供する。"""

    def __init__(
        self,
        repository: RequirementChangeLogRepository | None = None,
        document_repository: RequirementDocumentRepository | None = None,
        user_repository: UserRepository | None = None,
    ) -> None:
        """RequirementChangeLogServiceを初期化する。

        Args:
            repository: 要件定義変更履歴Repository。
            document_repository: 要件定義書Repository。
            user_repository: ユーザーRepository。
        """
        self.repository = repository or RequirementChangeLogRepository()
        self.document_repository = (
            document_repository or RequirementDocumentRepository()
        )
        self.user_repository = user_repository or UserRepository()

    def record(
        self,
        db: Session,
        *,
        document_id: int | None,
        target_type: str,
        target_id: int,
        action: str,
        field_name: str | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        reason: str | None = None,
        changed_by: int | None = None,
    ) -> RequirementChangeLog:
        """要件定義変更履歴を記録する。

        Args:
            db: DBセッション。
            document_id: 関連する要件定義書ID。
            target_type: 変更対象種別。
            target_id: 変更対象ID。
            action: 操作種別。
            field_name: 変更項目名。
            old_value: 変更前値。
            new_value: 変更後値。
            reason: 変更理由。
            changed_by: 変更ユーザーID。

        Returns:
            作成された要件定義変更履歴。
        """
        return self.repository.create(
            db,
            document_id=document_id,
            target_type=target_type,
            target_id=target_id,
            action=action,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            changed_by=changed_by,
        )

    def list_change_logs_paginated(
        self,
        db: Session,
        *,
        project_id: int,
        page: int,
        page_size: int,
        document_id: int | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        action: str | None = None,
        changed_by: int | None = None,
        changed_at_from: datetime | None = None,
        changed_at_to: datetime | None = None,
    ) -> tuple[list[RequirementChangeLog], int]:
        """プロジェクト内の要件定義変更履歴一覧を取得する。

        Args:
            db: DBセッション。
            project_id: 取得対象のプロジェクトID。
            page: 取得ページ番号。
            page_size: 1ページあたりの取得件数。
            document_id: 絞り込み対象の要件定義書ID。
            target_type: 絞り込み対象の変更対象種別。
            target_id: 絞り込み対象の変更対象ID。
            action: 絞り込み対象の操作種別。
            changed_by: 絞り込み対象の変更ユーザーID。
            changed_at_from: 変更日時の開始日時。
            changed_at_to: 変更日時の終了日時。

        Returns:
            要件定義変更履歴一覧と総件数。

        Raises:
            NotFoundError: 要件定義書が存在しない、またはプロジェクトに属さない場合。
        """
        storage_target_type = REQUIREMENT_CHANGE_LOG_FORMATTER.to_storage_target_type(
            target_type,
        )
        storage_action = REQUIREMENT_CHANGE_LOG_FORMATTER.to_storage_action(action)
        if document_id is not None:
            self._get_document_in_project(
                db,
                project_id=project_id,
                document_id=document_id,
            )
            return self.repository.list_paginated(
                db,
                page=page,
                page_size=page_size,
                document_id=document_id,
                target_type=storage_target_type,
                target_id=target_id,
                action=storage_action,
                changed_by=changed_by,
                changed_at_from=changed_at_from,
                changed_at_to=changed_at_to,
            )

        document_ids = self.document_repository.list_ids_by_project(db, project_id)
        if not document_ids:
            return [], 0

        return self.repository.list_paginated(
            db,
            page=page,
            page_size=page_size,
            document_ids=document_ids,
            target_type=storage_target_type,
            target_id=target_id,
            action=storage_action,
            changed_by=changed_by,
            changed_at_from=changed_at_from,
            changed_at_to=changed_at_to,
        )

    def build_change_log_reads(
        self,
        db: Session,
        change_logs: list[RequirementChangeLog],
    ) -> list[RequirementChangeLogRead]:
        """要件定義変更履歴レスポンス一覧を作成する。

        Args:
            db: DBセッション。
            change_logs: 要件定義変更履歴モデル一覧。

        Returns:
            要件定義変更履歴レスポンス一覧。
        """
        users_by_id = self._get_change_log_users_by_id(
            db,
            self._collect_change_log_user_ids(change_logs),
        )
        return [
            self._build_change_log_read(log, users_by_id=users_by_id)
            for log in change_logs
        ]

    def _build_change_log_read(
        self,
        change_log: RequirementChangeLog,
        *,
        users_by_id: dict[int, ChangeLogUserRead],
    ) -> RequirementChangeLogRead:
        """要件定義変更履歴レスポンスを作成する。"""
        field_name = REQUIREMENT_CHANGE_LOG_FORMATTER.normalize_field_name(
            change_log.field_name,
        )
        return RequirementChangeLogRead(
            id=change_log.id,
            document_id=change_log.document_id,
            target_type=self._normalize_target_type(change_log.target_type),
            target_id=change_log.target_id,
            action=self._normalize_action(change_log.action),
            field_name=field_name,
            old_value=REQUIREMENT_CHANGE_LOG_FORMATTER.extract_change_value(
                change_log.old_value,
                field_name,
                users_by_id=users_by_id,
            ),
            new_value=REQUIREMENT_CHANGE_LOG_FORMATTER.extract_change_value(
                change_log.new_value,
                field_name,
                users_by_id=users_by_id,
            ),
            reason=change_log.reason,
            changed_by=change_log.changed_by,
            changed_by_user=(
                users_by_id.get(change_log.changed_by)
                if change_log.changed_by is not None
                else None
            ),
            changed_at=change_log.changed_at,
        )

    def _normalize_action(self, action: str) -> str:
        """操作種別をAPI用の安定コードに変換する。"""
        return REQUIREMENT_CHANGE_LOG_FORMATTER.normalize_action(action)

    def _normalize_target_type(self, target_type: str) -> str:
        """対象種別をAPI用の安定コードに変換する。"""
        return REQUIREMENT_CHANGE_LOG_FORMATTER.normalize_target_type(target_type)

    def _collect_change_log_user_ids(
        self,
        change_logs: list[RequirementChangeLog],
    ) -> list[int | None]:
        """変更履歴レスポンス整形に必要なユーザーIDを集める。

        Args:
            change_logs: 要件定義変更履歴モデル一覧。

        Returns:
            表示補助に必要なユーザーID一覧。
        """
        user_ids: list[int | None] = [log.changed_by for log in change_logs]
        user_ids.extend(
            REQUIREMENT_CHANGE_LOG_FORMATTER.collect_user_ids(
                [
                    (log.field_name, log.old_value)
                    for log in change_logs
                ]
                + [
                    (log.field_name, log.new_value)
                    for log in change_logs
                ]
            )
        )
        return user_ids

    def _get_change_log_users_by_id(
        self,
        db: Session,
        user_ids: list[int | None],
    ) -> dict[int, ChangeLogUserRead]:
        """変更履歴に含まれるユーザー概要を取得する。"""
        ids = sorted({user_id for user_id in user_ids if user_id is not None})
        users = self.user_repository.list_by_ids(db, ids)
        return {
            user.id: ChangeLogUserRead(
                id=user.id,
                name=user.name,
                email=user.email,
                avatar_url=None,
            )
            for user in users
        }

    def _get_document_in_project(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
    ) -> object:
        """プロジェクト内の要件定義書を取得する。"""
        document = self.document_repository.get_by_id(db, document_id)
        if document is None or document.project_id != project_id:
            raise NotFoundError(error_messages.REQUIREMENT_DOCUMENT_NOT_FOUND)
        return document
