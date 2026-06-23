"""要件定義承認サービスを定義するモジュール。"""

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.requirement import RequirementApproval, RequirementDocument
from app.repositories.requirement import (
    RequirementApprovalRepository,
    RequirementDocumentRepository,
    RequirementOpenIssueRepository,
    RequirementRepository,
    RequirementSectionRepository,
)
from app.schemas.requirement import (
    RequirementApprovalDecisionCreate,
    RequirementApprovalRequestCreate,
)
from app.services.requirement_change_log import (
    RequirementChangeLogService,
)


class RequirementApprovalAction:
    """要件定義承認の変更履歴操作種別定数。"""

    REQUESTED = "approval.requested"
    APPROVED = "approval.approved"
    REJECTED = "approval.rejected"


class RequirementApprovalTargetType:
    """要件定義承認の対象種別定数。"""

    DOCUMENT = "document"
    SECTION = "section"
    REQUIREMENT_ITEM = "requirement_item"
    OPEN_ISSUE = "open_issue"


class RequirementApprovalService:
    """要件定義承認に関するビジネスロジックを提供する。"""

    def __init__(
        self,
        repository: RequirementApprovalRepository | None = None,
        document_repository: RequirementDocumentRepository | None = None,
        section_repository: RequirementSectionRepository | None = None,
        requirement_repository: RequirementRepository | None = None,
        open_issue_repository: RequirementOpenIssueRepository | None = None,
        change_log_service: RequirementChangeLogService | None = None,
    ) -> None:
        """RequirementApprovalServiceを初期化する。

        Args:
            repository: 要件定義承認Repository。
            document_repository: 要件定義書Repository。
            section_repository: 要件定義セクションRepository。
            requirement_repository: 要件Repository。
            open_issue_repository: 未決事項Repository。
            change_log_service: 要件定義変更履歴Service。
        """
        self.repository = repository or RequirementApprovalRepository()
        self.document_repository = (
            document_repository or RequirementDocumentRepository()
        )
        self.section_repository = section_repository or RequirementSectionRepository()
        self.requirement_repository = (
            requirement_repository or RequirementRepository()
        )
        self.open_issue_repository = (
            open_issue_repository or RequirementOpenIssueRepository()
        )
        self.change_log_service = change_log_service or RequirementChangeLogService()

    def request_approval(
        self,
        db: Session,
        *,
        project_id: int,
        approval_in: RequirementApprovalRequestCreate,
        requested_by: int,
    ) -> RequirementApproval:
        """要件定義対象の承認を申請する。

        Args:
            db: DBセッション。
            project_id: 申請対象のプロジェクトID。
            approval_in: 承認申請入力値。
            requested_by: 申請ユーザーID。

        Returns:
            作成された承認申請。

        Raises:
            NotFoundError: 対象が存在しない、またはプロジェクトに属さない場合。
        """
        document = self._get_target_document(
            db,
            project_id=project_id,
            target_type=approval_in.target_type,
            target_id=approval_in.target_id,
        )
        approval = self.repository.create(
            db,
            document_id=document.id,
            approval_in=approval_in,
            requested_by=requested_by,
        )
        self._record_change_log(
            db,
            approval=approval,
            action=RequirementApprovalAction.REQUESTED,
            new_value=self._build_approval_snapshot(approval),
            changed_by=requested_by,
        )
        return approval

    def list_approvals_paginated(
        self,
        db: Session,
        *,
        project_id: int,
        page: int,
        page_size: int,
        target_type: str | None = None,
        target_id: int | None = None,
        status: str | None = None,
        approver_id: int | None = None,
    ) -> tuple[list[RequirementApproval], int]:
        """プロジェクト内の承認一覧をページング付きで取得する。

        Args:
            db: DBセッション。
            project_id: 一覧取得対象のプロジェクトID。
            page: 取得ページ番号。
            page_size: 1ページあたりの取得件数。
            target_type: 絞り込み対象の対象種別。
            target_id: 絞り込み対象の対象ID。
            status: 絞り込み対象のステータス。
            approver_id: 絞り込み対象の承認者ID。

        Returns:
            承認一覧と総件数。
        """
        if target_type is not None and target_id is not None:
            document = self._get_target_document(
                db,
                project_id=project_id,
                target_type=target_type,
                target_id=target_id,
            )
            document_ids = [document.id]
        else:
            document_ids = self.document_repository.list_ids_by_project(db, project_id)

        if not document_ids:
            return [], 0

        return self.repository.list_paginated(
            db,
            document_ids=document_ids,
            page=page,
            page_size=page_size,
            target_type=target_type,
            target_id=target_id,
            status=status,
            approver_id=approver_id,
        )

    def approve(
        self,
        db: Session,
        *,
        project_id: int,
        approval_id: int,
        decision_in: RequirementApprovalDecisionCreate,
        actor_id: int,
    ) -> RequirementApproval:
        """承認申請を承認する。"""
        approval = self._get_approval_in_project(
            db,
            project_id=project_id,
            approval_id=approval_id,
        )
        self._raise_if_decided(approval)
        before_value = self._build_approval_snapshot(approval)
        updated_approval = self.repository.mark_approved(
            db,
            approval=approval,
            comment=decision_in.comment,
        )
        self._record_change_log(
            db,
            approval=updated_approval,
            action=RequirementApprovalAction.APPROVED,
            old_value=before_value,
            new_value=self._build_approval_snapshot(updated_approval),
            changed_by=actor_id,
        )
        return updated_approval

    def reject(
        self,
        db: Session,
        *,
        project_id: int,
        approval_id: int,
        decision_in: RequirementApprovalDecisionCreate,
        actor_id: int,
    ) -> RequirementApproval:
        """承認申請を差し戻す。"""
        approval = self._get_approval_in_project(
            db,
            project_id=project_id,
            approval_id=approval_id,
        )
        self._raise_if_decided(approval)
        before_value = self._build_approval_snapshot(approval)
        updated_approval = self.repository.mark_rejected(
            db,
            approval=approval,
            comment=decision_in.comment,
        )
        self._record_change_log(
            db,
            approval=updated_approval,
            action=RequirementApprovalAction.REJECTED,
            old_value=before_value,
            new_value=self._build_approval_snapshot(updated_approval),
            changed_by=actor_id,
        )
        return updated_approval

    def _get_approval_in_project(
        self,
        db: Session,
        *,
        project_id: int,
        approval_id: int,
    ) -> RequirementApproval:
        """プロジェクト内の承認申請を取得する。"""
        approval = self.repository.get_by_id(db, approval_id)
        if approval is None:
            raise NotFoundError(error_messages.REQUIREMENT_APPROVAL_NOT_FOUND)
        document = self.document_repository.get_by_id(db, approval.document_id)
        if document is None or document.project_id != project_id:
            raise NotFoundError(error_messages.REQUIREMENT_APPROVAL_NOT_FOUND)
        return approval

    def _get_target_document(
        self,
        db: Session,
        *,
        project_id: int,
        target_type: str,
        target_id: int,
    ) -> RequirementDocument:
        """承認対象が属する要件定義書を取得する。"""
        if target_type == RequirementApprovalTargetType.DOCUMENT:
            document = self.document_repository.get_by_id(db, target_id)
        elif target_type == RequirementApprovalTargetType.SECTION:
            section = self.section_repository.get_by_id(db, target_id)
            document = (
                self.document_repository.get_by_id(db, section.document_id)
                if section is not None
                else None
            )
        elif target_type == RequirementApprovalTargetType.REQUIREMENT_ITEM:
            requirement = self.requirement_repository.get_by_id(db, target_id)
            document = (
                self.document_repository.get_by_id(db, requirement.document_id)
                if requirement is not None
                else None
            )
        elif target_type == RequirementApprovalTargetType.OPEN_ISSUE:
            issue = self.open_issue_repository.get_by_id(db, target_id)
            document = (
                self.document_repository.get_by_id(db, issue.document_id)
                if issue is not None
                else None
            )
        else:
            raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)

        if document is None or document.project_id != project_id:
            raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)
        return document

    def _raise_if_decided(self, approval: RequirementApproval) -> None:
        """承認申請が判断済みであれば例外を送出する。"""
        if approval.status != "requested":
            raise BadRequestError(error_messages.REQUIREMENT_APPROVAL_ALREADY_DECIDED)

    def _record_change_log(
        self,
        db: Session,
        *,
        approval: RequirementApproval,
        action: str,
        old_value: dict[str, object] | None = None,
        new_value: dict[str, object] | None = None,
        changed_by: int | None = None,
    ) -> None:
        """承認申請の変更履歴を記録する。"""
        self.change_log_service.record(
            db,
            document_id=approval.document_id,
            target_type=approval.target_type,
            target_id=approval.target_id,
            action=action,
            old_value=old_value,
            new_value=new_value,
            changed_by=changed_by,
        )

    def _build_approval_snapshot(
        self,
        approval: RequirementApproval,
    ) -> dict[str, object]:
        """変更履歴に保存する承認申請スナップショットを作成する。"""
        return {
            "id": approval.id,
            "document_id": approval.document_id,
            "target_type": approval.target_type,
            "target_id": approval.target_id,
            "status": approval.status,
            "approver_id": approval.approver_id,
            "requested_by": approval.requested_by,
            "approved_at": approval.approved_at.isoformat()
            if approval.approved_at is not None
            else None,
            "rejected_at": approval.rejected_at.isoformat()
            if approval.rejected_at is not None
            else None,
            "comment": approval.comment,
        }
