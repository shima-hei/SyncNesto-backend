"""要件定義Repositoryを定義するモジュール。"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.requirement import (
    RequirementApproval,
)
from app.schemas.requirement import (
    RequirementApprovalRequestCreate,
)


class RequirementApprovalRepository:
    """RequirementApprovalテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        document_id: int,
        approval_in: RequirementApprovalRequestCreate,
        requested_by: int,
    ) -> RequirementApproval:
        """要件定義承認申請を作成する。"""
        approval = RequirementApproval(
            document_id=document_id,
            target_type=approval_in.target_type,
            target_id=approval_in.target_id,
            status="requested",
            approver_id=approval_in.approver_id,
            requested_by=requested_by,
            comment=approval_in.comment,
        )
        db.add(approval)
        db.commit()
        db.refresh(approval)
        return approval

    def get_by_id(self, db: Session, approval_id: int) -> RequirementApproval | None:
        """idに一致する要件定義承認を取得する。"""
        return (
            db.query(RequirementApproval)
            .filter(RequirementApproval.id == approval_id)
            .first()
        )

    def list_paginated(
        self,
        db: Session,
        *,
        document_ids: list[int],
        page: int,
        page_size: int,
        target_type: str | None = None,
        target_id: int | None = None,
        status: str | None = None,
        approver_id: int | None = None,
    ) -> tuple[list[RequirementApproval], int]:
        """指定要件定義書群の承認一覧をページング付きで取得する。"""
        query = db.query(RequirementApproval).filter(
            RequirementApproval.document_id.in_(document_ids)
        )
        if target_type is not None:
            query = query.filter(RequirementApproval.target_type == target_type)
        if target_id is not None:
            query = query.filter(RequirementApproval.target_id == target_id)
        if status is not None:
            query = query.filter(RequirementApproval.status == status)
        if approver_id is not None:
            query = query.filter(RequirementApproval.approver_id == approver_id)

        total = query.count()
        approvals = (
            query.order_by(RequirementApproval.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return approvals, total

    def mark_approved(
        self,
        db: Session,
        *,
        approval: RequirementApproval,
        comment: str | None = None,
    ) -> RequirementApproval:
        """要件定義承認を承認済みに更新する。"""
        approval.status = "approved"
        approval.approved_at = datetime.now(UTC)
        approval.rejected_at = None
        if comment is not None:
            approval.comment = comment
        db.commit()
        db.refresh(approval)
        return approval

    def mark_rejected(
        self,
        db: Session,
        *,
        approval: RequirementApproval,
        comment: str | None = None,
    ) -> RequirementApproval:
        """要件定義承認を差し戻し済みに更新する。"""
        approval.status = "rejected"
        approval.rejected_at = datetime.now(UTC)
        approval.approved_at = None
        if comment is not None:
            approval.comment = comment
        db.commit()
        db.refresh(approval)
        return approval
