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
    ) -> None:
        """RequirementChangeLogServiceを初期化する。

        Args:
            repository: 要件定義変更履歴Repository。
            document_repository: 要件定義書Repository。
        """
        self.repository = repository or RequirementChangeLogRepository()
        self.document_repository = (
            document_repository or RequirementDocumentRepository()
        )

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
                target_type=target_type,
                target_id=target_id,
                action=action,
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
            target_type=target_type,
            target_id=target_id,
            action=action,
            changed_by=changed_by,
            changed_at_from=changed_at_from,
            changed_at_to=changed_at_to,
        )

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
