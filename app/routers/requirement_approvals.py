"""要件定義承認APIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.routers import requirements_shared as shared
from app.schemas.requirement import (
    RequirementApprovalDecisionCreate,
    RequirementApprovalListResponse,
    RequirementApprovalRead,
    RequirementApprovalRequestCreate,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["requirements"])


@router.get(
    "/approvals",
    response_model=RequirementApprovalListResponse,
)
def list_requirement_approvals(
    project_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    target_type: str | None = Query(default=None),
    target_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    approver_id: int | None = Query(default=None),
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> RequirementApprovalListResponse:
    """要件定義承認一覧を取得する。

    Args:
        project_id: 一覧取得対象のプロジェクトID。
        page: 取得ページ番号。
        page_size: 1ページあたりの取得件数。
        target_type: 絞り込み対象の対象種別。
        target_id: 絞り込み対象の対象ID。
        status: 絞り込み対象のステータス。
        approver_id: 絞り込み対象の承認者ID。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        要件定義承認のページング済み一覧。
    """
    approvals, total = shared.approval_service.list_approvals_paginated(
        db,
        project_id=project_id,
        page=page,
        page_size=page_size,
        target_type=target_type,
        target_id=target_id,
        status=status,
        approver_id=approver_id,
    )
    return RequirementApprovalListResponse(
        items=[
            RequirementApprovalRead.model_validate(approval)
            for approval in approvals
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/approvals/request",
    response_model=RequirementApprovalRead,
    status_code=status.HTTP_201_CREATED,
)
def request_requirement_approval(
    project_id: int,
    approval_in: RequirementApprovalRequestCreate,
    current_user: User = Depends(require_project_permission("requirement:review")),
    db: Session = Depends(get_db),
) -> RequirementApprovalRead:
    """要件定義対象の承認を申請する。

    Args:
        project_id: 申請対象のプロジェクトID。
        approval_in: 承認申請入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        作成された承認申請。
    """
    approval = shared.approval_service.request_approval(
        db,
        project_id=project_id,
        approval_in=approval_in,
        requested_by=current_user.id,
    )
    return RequirementApprovalRead.model_validate(approval)


@router.post(
    "/approvals/{approval_id}/approve",
    response_model=RequirementApprovalRead,
)
def approve_requirement_approval(
    project_id: int,
    approval_id: int,
    decision_in: RequirementApprovalDecisionCreate,
    current_user: User = Depends(require_project_permission("requirement:review")),
    db: Session = Depends(get_db),
) -> RequirementApprovalRead:
    """要件定義承認申請を承認する。

    Args:
        project_id: 承認対象のプロジェクトID。
        approval_id: 承認申請ID。
        decision_in: 承認判断入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        承認済みの承認申請。
    """
    approval = shared.approval_service.approve(
        db,
        project_id=project_id,
        approval_id=approval_id,
        decision_in=decision_in,
        actor_id=current_user.id,
    )
    return RequirementApprovalRead.model_validate(approval)


@router.post(
    "/approvals/{approval_id}/reject",
    response_model=RequirementApprovalRead,
)
def reject_requirement_approval(
    project_id: int,
    approval_id: int,
    decision_in: RequirementApprovalDecisionCreate,
    current_user: User = Depends(require_project_permission("requirement:review")),
    db: Session = Depends(get_db),
) -> RequirementApprovalRead:
    """要件定義承認申請を差し戻す。

    Args:
        project_id: 差し戻し対象のプロジェクトID。
        approval_id: 承認申請ID。
        decision_in: 承認判断入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        差し戻し済みの承認申請。
    """
    approval = shared.approval_service.reject(
        db,
        project_id=project_id,
        approval_id=approval_id,
        decision_in=decision_in,
        actor_id=current_user.id,
    )
    return RequirementApprovalRead.model_validate(approval)
