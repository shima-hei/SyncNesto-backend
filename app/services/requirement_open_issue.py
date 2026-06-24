"""要件定義未決事項サービスを定義するモジュール。"""

from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import DuplicateResourceError, NotFoundError
from app.models.requirement import (
    Requirement,
    RequirementDocument,
    RequirementOpenIssue,
    RequirementSection,
)
from app.repositories.requirement import (
    RequirementDocumentRepository,
    RequirementOpenIssueRepository,
    RequirementRepository,
    RequirementSectionRepository,
)
from app.schemas.requirement import (
    RequirementCreate,
    RequirementOpenIssueCreate,
    RequirementOpenIssuePromoteCreate,
    RequirementOpenIssueRead,
    RequirementOpenIssueUpdate,
)
from app.services.conflict import (
    raise_duplicate_after_rollback,
    raise_if_version_conflict,
)
from app.services.requirement_change_log import (
    RequirementChangeLogAction,
    RequirementChangeLogService,
    RequirementChangeLogTargetType,
)

AUTO_OPEN_ISSUE_CODE_PREFIX = "ISSUE"
AUTO_OPEN_ISSUE_CODE_RETRY_LIMIT = 5
AUTO_PROMOTED_REQUIREMENT_CODE_RETRY_LIMIT = 5


class RequirementOpenIssueService:
    """要件定義未決事項に関するビジネスロジックを提供する。"""

    def __init__(
        self,
        repository: RequirementOpenIssueRepository | None = None,
        document_repository: RequirementDocumentRepository | None = None,
        requirement_repository: RequirementRepository | None = None,
        section_repository: RequirementSectionRepository | None = None,
        change_log_service: RequirementChangeLogService | None = None,
    ) -> None:
        """RequirementOpenIssueServiceを初期化する。

        Args:
            repository: 未決事項Repository。
            document_repository: 要件定義書Repository。
            requirement_repository: 要件Repository。
            section_repository: 要件定義セクションRepository。
            change_log_service: 要件定義変更履歴サービス。
        """
        self.repository = repository or RequirementOpenIssueRepository()
        self.document_repository = (
            document_repository or RequirementDocumentRepository()
        )
        self.requirement_repository = requirement_repository or RequirementRepository()
        self.section_repository = section_repository or RequirementSectionRepository()
        self.change_log_service = change_log_service or RequirementChangeLogService()

    def create_open_issue(
        self,
        db: Session,
        *,
        project_id: int,
        issue_in: RequirementOpenIssueCreate,
        actor_id: int | None = None,
    ) -> RequirementOpenIssue:
        """未決事項を作成する。

        Args:
            db: DBセッション。
            project_id: 作成対象のプロジェクトID。
            issue_in: 未決事項作成入力値。
            actor_id: 操作ユーザーID。

        Returns:
            作成された未決事項。

        Raises:
            NotFoundError: 要件定義書または関連要件が存在しない場合。
            DuplicateResourceError: issue_codeが重複している場合。
        """
        self._get_document_in_project(
            db,
            project_id=project_id,
            document_id=issue_in.document_id,
        )
        if issue_in.related_requirement_id is not None:
            self._get_requirement_in_document(
                db,
                document_id=issue_in.document_id,
                requirement_id=issue_in.related_requirement_id,
            )
        requested_issue_code = self._normalize_issue_code(issue_in.issue_code)
        if (
            requested_issue_code
            and self.repository.get_by_document_issue_code(
                db,
                document_id=issue_in.document_id,
                issue_code=requested_issue_code,
            )
        ):
            raise DuplicateResourceError(
                error_messages.REQUIREMENT_OPEN_ISSUE_CODE_ALREADY_EXISTS
            )

        retry_limit = 1 if requested_issue_code else AUTO_OPEN_ISSUE_CODE_RETRY_LIMIT
        for attempt in range(retry_limit):
            issue_code = requested_issue_code or self._generate_issue_code(
                db,
                issue_in.document_id,
            )
            normalized = issue_in.model_copy(update={"issue_code": issue_code})
            try:
                issue = self.repository.create(
                    db,
                    issue_in=normalized,
                    actor_id=actor_id,
                )
            except IntegrityError as exc:
                db.rollback()
                if requested_issue_code or attempt == retry_limit - 1:
                    raise_duplicate_after_rollback(
                        db,
                        error_messages.REQUIREMENT_OPEN_ISSUE_CODE_ALREADY_EXISTS,
                        exc,
                    )
                continue
            break
        else:
            raise DuplicateResourceError(
                error_messages.REQUIREMENT_OPEN_ISSUE_CODE_ALREADY_EXISTS
            )

        self.change_log_service.record(
            db,
            document_id=issue.document_id,
            target_type=RequirementChangeLogTargetType.OPEN_ISSUE,
            target_id=issue.id,
            action=RequirementChangeLogAction.CREATED,
            new_value=self._build_issue_snapshot(issue),
            changed_by=actor_id,
        )
        return issue

    def list_open_issues_paginated(
        self,
        db: Session,
        *,
        project_id: int,
        page: int,
        page_size: int,
        document_id: int | None = None,
        q: str | None = None,
        status: str | None = None,
        assignee_id: int | None = None,
        due_date_from: date | None = None,
        due_date_to: date | None = None,
        related_requirement_id: int | None = None,
    ) -> tuple[list[RequirementOpenIssue], int]:
        """プロジェクト内の未決事項一覧をページング付きで取得する。"""
        if document_id is not None:
            self._get_document_in_project(
                db,
                project_id=project_id,
                document_id=document_id,
            )
            document_ids = [document_id]
        else:
            document_ids = self.document_repository.list_ids_by_project(db, project_id)

        if not document_ids:
            return [], 0

        return self.repository.list_paginated(
            db,
            document_ids=document_ids,
            page=page,
            page_size=page_size,
            q=q,
            status=status,
            assignee_id=assignee_id,
            due_date_from=due_date_from,
            due_date_to=due_date_to,
            related_requirement_id=related_requirement_id,
        )

    def get_open_issue(
        self,
        db: Session,
        *,
        project_id: int,
        issue_id: int,
    ) -> RequirementOpenIssue:
        """未決事項を取得する。"""
        issue = self.repository.get_by_id(db, issue_id)
        if issue is None:
            raise NotFoundError(error_messages.REQUIREMENT_OPEN_ISSUE_NOT_FOUND)
        self._get_document_in_project(
            db,
            project_id=project_id,
            document_id=issue.document_id,
        )
        return issue

    def update_open_issue(
        self,
        db: Session,
        *,
        project_id: int,
        issue_id: int,
        issue_in: RequirementOpenIssueUpdate,
        actor_id: int | None = None,
    ) -> RequirementOpenIssue:
        """未決事項を更新する。"""
        issue = self.get_open_issue(db, project_id=project_id, issue_id=issue_id)
        raise_if_version_conflict(
            current_version=issue.version,
            requested_version=issue_in.version,
            current=RequirementOpenIssueRead.model_validate(issue).model_dump(
                mode="json",
            ),
        )
        if (
            "related_requirement_id" in issue_in.model_fields_set
            and issue_in.related_requirement_id is not None
        ):
            self._get_requirement_in_document(
                db,
                document_id=issue.document_id,
                requirement_id=issue_in.related_requirement_id,
            )

        before_value = self._build_issue_snapshot(issue)
        try:
            updated_issue = self.repository.update(
                db,
                issue=issue,
                issue_in=issue_in,
                actor_id=actor_id,
            )
        except IntegrityError as exc:
            raise_duplicate_after_rollback(
                db,
                error_messages.REQUIREMENT_OPEN_ISSUE_CODE_ALREADY_EXISTS,
                exc,
            )

        self.change_log_service.record(
            db,
            document_id=updated_issue.document_id,
            target_type=RequirementChangeLogTargetType.OPEN_ISSUE,
            target_id=updated_issue.id,
            action=RequirementChangeLogAction.UPDATED,
            old_value=before_value,
            new_value=self._build_issue_snapshot(updated_issue),
            reason=issue_in.reason,
            changed_by=actor_id,
        )
        return updated_issue

    def delete_open_issue(
        self,
        db: Session,
        *,
        project_id: int,
        issue_id: int,
        actor_id: int | None = None,
    ) -> None:
        """未決事項を論理削除する。"""
        issue = self.get_open_issue(db, project_id=project_id, issue_id=issue_id)
        before_value = self._build_issue_snapshot(issue)
        deleted_issue = self.repository.soft_delete(
            db,
            issue=issue,
            actor_id=actor_id,
        )
        self.change_log_service.record(
            db,
            document_id=deleted_issue.document_id,
            target_type=RequirementChangeLogTargetType.OPEN_ISSUE,
            target_id=deleted_issue.id,
            action=RequirementChangeLogAction.DELETED,
            old_value=before_value,
            changed_by=actor_id,
        )

    def promote_to_requirement(
        self,
        db: Session,
        *,
        project_id: int,
        issue_id: int,
        promote_in: RequirementOpenIssuePromoteCreate,
        actor_id: int | None = None,
    ) -> Requirement:
        """未決事項を要件へ昇格する。"""
        issue = self.get_open_issue(db, project_id=project_id, issue_id=issue_id)
        raise_if_version_conflict(
            current_version=issue.version,
            requested_version=promote_in.version,
            current=RequirementOpenIssueRead.model_validate(issue).model_dump(
                mode="json",
            ),
        )
        if promote_in.section_id is not None:
            self._get_section_in_document(
                db,
                document_id=issue.document_id,
                section_id=promote_in.section_id,
            )
        for attempt in range(AUTO_PROMOTED_REQUIREMENT_CODE_RETRY_LIMIT):
            requirement_in = RequirementCreate(
                document_id=issue.document_id,
                section_id=promote_in.section_id,
                requirement_code=self._generate_requirement_code(
                    db,
                    issue.document_id,
                ),
                requirement_type=promote_in.requirement_type,
                category=promote_in.category,
                title=promote_in.title or issue.title,
                description=promote_in.description or issue.description,
                rationale=promote_in.rationale,
                acceptance_criteria=promote_in.acceptance_criteria,
                priority=promote_in.priority,
                status=promote_in.status,
                source=promote_in.source or issue.issue_code,
                owner_id=promote_in.owner_id or issue.assignee_id,
            )
            try:
                requirement = self.requirement_repository.create(
                    db,
                    requirement_in=requirement_in,
                    actor_id=actor_id,
                )
            except IntegrityError as exc:
                db.rollback()
                if attempt == AUTO_PROMOTED_REQUIREMENT_CODE_RETRY_LIMIT - 1:
                    raise_duplicate_after_rollback(
                        db,
                        error_messages.REQUIREMENT_CODE_ALREADY_EXISTS,
                        exc,
                    )
                continue
            break
        else:
            raise DuplicateResourceError(
                error_messages.REQUIREMENT_CODE_ALREADY_EXISTS
            )

        before_value = self._build_issue_snapshot(issue)
        updated_issue = self.repository.mark_promoted(
            db,
            issue=issue,
            requirement_id=requirement.id,
            resolution=promote_in.resolution,
            actor_id=actor_id,
        )
        self.change_log_service.record(
            db,
            document_id=updated_issue.document_id,
            target_type=RequirementChangeLogTargetType.OPEN_ISSUE,
            target_id=updated_issue.id,
            action=RequirementChangeLogAction.PROMOTED_TO_REQUIREMENT,
            old_value=before_value,
            new_value={
                "issue": self._build_issue_snapshot(updated_issue),
                "requirement_id": requirement.id,
                "requirement_code": requirement.requirement_code,
            },
            reason=promote_in.reason,
            changed_by=actor_id,
        )
        return requirement

    def _normalize_issue_code(self, issue_code: str | None) -> str | None:
        """リクエスト由来の未決事項IDを正規化する。

        Args:
            issue_code: リクエストで受け取った未決事項ID。

        Returns:
            前後空白を除去した未決事項ID。空文字またはNoneの場合はNone。
        """
        if issue_code is None:
            return None
        stripped = issue_code.strip()
        return stripped or None

    def _generate_issue_code(self, db: Session, document_id: int) -> str:
        """要件定義書内で次の未決事項IDを採番する。

        Args:
            db: DBセッション。
            document_id: 採番対象の要件定義書ID。

        Returns:
            `ISSUE-001` 形式の未決事項ID。
        """
        next_number = self.repository.get_max_auto_issue_number(db, document_id) + 1
        return f"{AUTO_OPEN_ISSUE_CODE_PREFIX}-{next_number:03d}"

    def _generate_requirement_code(self, db: Session, document_id: int) -> str:
        """要件定義書内で次の昇格先要件コードを採番する。

        Args:
            db: DBセッション。
            document_id: 採番対象の要件定義書ID。

        Returns:
            `REQ-001` 形式の要件コード。
        """
        next_number = (
            self.requirement_repository.get_max_auto_requirement_number(
                db,
                document_id,
            )
            + 1
        )
        return f"REQ-{next_number:03d}"

    def _get_document_in_project(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
    ) -> RequirementDocument:
        """プロジェクト内の要件定義書を取得する。"""
        document = self.document_repository.get_by_id(db, document_id)
        if document is None or document.project_id != project_id:
            raise NotFoundError(error_messages.REQUIREMENT_DOCUMENT_NOT_FOUND)
        return document

    def _get_requirement_in_document(
        self,
        db: Session,
        *,
        document_id: int,
        requirement_id: int,
    ) -> Requirement:
        """要件定義書内の要件を取得する。"""
        requirement = self.requirement_repository.get_by_id(db, requirement_id)
        if requirement is None or requirement.document_id != document_id:
            raise NotFoundError(error_messages.REQUIREMENT_NOT_FOUND)
        return requirement

    def _get_section_in_document(
        self,
        db: Session,
        *,
        document_id: int,
        section_id: int,
    ) -> RequirementSection:
        """要件定義書内のセクションを取得する。"""
        section = self.section_repository.get_by_id(db, section_id)
        if section is None or section.document_id != document_id:
            raise NotFoundError(error_messages.REQUIREMENT_SECTION_NOT_FOUND)
        return section

    def _build_issue_snapshot(
        self,
        issue: RequirementOpenIssue,
    ) -> dict[str, object]:
        """変更履歴に保存する未決事項スナップショットを作成する。"""
        return RequirementOpenIssueRead.model_validate(issue).model_dump(mode="json")
