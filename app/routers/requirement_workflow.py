"""要件定義書のレビュー依頼をプレビューし、発行・閲覧する。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.artifact_release import ArtifactNodePage
from app.schemas.requirement_workflow import (
    RequirementBaselineComparison,
    RequirementFormalApprovalCreate,
    RequirementFormalApprovalDecisionInput,
    RequirementFormalApprovalDecisionPreview,
    RequirementFormalApprovalInput,
    RequirementFormalApprovalPreview,
    RequirementItemWorkflowState,
    RequirementReviewConfirmationInput,
    RequirementReviewInput,
    RequirementReviewManagementRead,
    RequirementReviewPreview,
    RequirementReviewProgress,
    RequirementReviewRequestCreate,
    RequirementReviewSubmitInput,
    RequirementReviewThreadActionInput,
    RequirementReviewThreadCreate,
    RequirementReviewThreadDetail,
    RequirementReviewThreadList,
    RequirementReviewThreadRead,
    RequirementReviewVersionInput,
    RequirementWorkflowList,
    RequirementWorkflowRead,
    RequirementWorkflowRecipient,
    WorkflowKind,
    WorkflowListView,
    WorkflowStatus,
)
from app.services.requirement_formal_approval import RequirementFormalApprovalService
from app.services.requirement_item_workflow import RequirementItemWorkflowService
from app.services.requirement_workflow import RequirementWorkflowService
from app.services.requirement_workflow_review import RequirementWorkflowReviewService

router = APIRouter(prefix="/projects/{project_id}", tags=["requirement-workflow"])
Db = Annotated[Session, Depends(get_db)]
Reader = Annotated[User, Depends(require_project_permission("requirement:read"))]
Issuer = Annotated[User, Depends(require_project_permission("requirement:review"))]
service = RequirementWorkflowService()
review = RequirementWorkflowReviewService(service)
formal_approval = RequirementFormalApprovalService(service)
item_workflow = RequirementItemWorkflowService(service.releases)


@router.get(
    "/requirement-workflow/item-states",
    response_model=list[RequirementItemWorkflowState],
)
def read_requirement_item_workflow_states(
    project_id: int,
    db: Db,
    user: Reader,
    requirement_ids: Annotated[
        list[Annotated[int, Query(gt=0)]], Query(min_length=1, max_length=100)
    ],
):
    """表示中の要件に対応する作成・レビュー確認・正式承認を取得する。"""
    return item_workflow.read(db, user, project_id, requirement_ids)


@router.get(
    "/requirement-workflow/recipients",
    response_model=list[RequirementWorkflowRecipient],
)
def list_review_recipients(project_id: int, db: Db, user: Issuer):
    """レビュー担当者に指定できる社内メンバーを取得する。"""
    return service.candidates(db, user, project_id)


@router.get(
    "/requirement-workflow/formal-approval-recipients",
    response_model=list[RequirementWorkflowRecipient],
)
def list_formal_approval_recipients(project_id: int, db: Db, user: Issuer):
    """現在この案件で正式承認できる、申請者以外の社内候補を取得する。"""
    return formal_approval.candidates(db, user, project_id)


@router.post(
    "/requirement-documents/{document_id}/formal-approval-requests/preview",
    response_model=RequirementFormalApprovalPreview,
)
def preview_requirement_formal_approval(
    project_id: int,
    document_id: int,
    data: RequirementFormalApprovalInput,
    db: Db,
    user: Issuer,
):
    """文書全体・承認者・申請前の条件を確認する。"""
    return formal_approval.preview(db, user, project_id, document_id, data)


@router.post(
    "/requirement-documents/{document_id}/formal-approval-requests",
    response_model=RequirementWorkflowRead,
    status_code=status.HTTP_201_CREATED,
)
def create_requirement_formal_approval(
    project_id: int,
    document_id: int,
    data: RequirementFormalApprovalCreate,
    db: Db,
    user: Issuer,
    response: Response,
):
    """確認した条件を再検証し、文書全体を版として固定して申請する。"""
    result, created = formal_approval.create(db, user, project_id, document_id, data)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return result


@router.get(
    "/requirement-documents/{document_id}/formal-approval-baseline",
    response_model=RequirementWorkflowRead | None,
)
def read_requirement_formal_approval_baseline(
    project_id: int,
    document_id: int,
    db: Db,
    user: Reader,
):
    """最後に正式承認された開発基準を取得する。"""
    return formal_approval.baseline(db, user, project_id, document_id)


@router.post(
    "/requirement-documents/{document_id}/review-requests/preview",
    response_model=RequirementReviewPreview,
)
def preview_requirement_review(
    project_id: int,
    document_id: int,
    data: RequirementReviewInput,
    db: Db,
    user: Issuer,
):
    """版を保存せずに、レビュー対象と担当者を確認する。"""
    return service.preview(db, user, project_id, document_id, data)


@router.post(
    "/requirement-documents/{document_id}/review-requests",
    response_model=RequirementWorkflowRead,
    status_code=status.HTTP_201_CREATED,
)
def create_requirement_review(
    project_id: int,
    document_id: int,
    data: RequirementReviewRequestCreate,
    db: Db,
    user: Issuer,
    response: Response,
):
    """確認済みの版・依頼・担当者を一括で作成する。"""
    result, created = service.create(db, user, project_id, document_id, data)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return result


@router.get(
    "/requirement-documents/{document_id}/review-management",
    response_model=RequirementReviewManagementRead,
)
def read_requirement_review_management(
    project_id: int,
    document_id: int,
    db: Db,
    user: Reader,
    response: Response,
    resolved: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=30),
):
    """担当者、正式承認、指摘と未決事項の集計を表示する。"""
    response.headers["Cache-Control"] = "private, no-store"
    return service.management(
        db, user, project_id, document_id, resolved, page, page_size
    )


@router.get(
    "/requirement-documents/{document_id}/baseline-comparison",
    response_model=RequirementBaselineComparison | None,
)
def read_requirement_baseline_comparison(
    project_id: int,
    document_id: int,
    db: Db,
    user: Reader,
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=15, ge=1, le=50),
):
    """現在の開発基準と保存済みの編集内容の変更点を取得する。"""
    response.headers["Cache-Control"] = "private, no-store"
    return service.baseline_comparison(
        db, user, project_id, document_id, page, page_size
    )


@router.get("/requirement-workflow", response_model=RequirementWorkflowList)
def list_requirement_workflow(
    project_id: int,
    db: Db,
    user: Reader,
    document_id: int | None = Query(default=None, gt=0),
    view: WorkflowListView = "all",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    kind: WorkflowKind | None = None,
    status: WorkflowStatus | None = None,
    q: str | None = Query(default=None, max_length=100),
):
    """案件または定義書の依頼を、担当・発行者で絞って取得する。"""
    return service.list(
        db,
        user,
        project_id,
        document_id,
        view,
        page,
        page_size,
        kind=kind,
        status=status,
        search=q.strip() if q else None,
    )


@router.get(
    "/requirement-workflow/{request_id}", response_model=RequirementWorkflowRead
)
def read_requirement_workflow(project_id: int, request_id: UUID, db: Db, user: Reader):
    """指定された依頼と対象版の情報を取得する。"""
    return service.get(db, user, project_id, request_id)


@router.get(
    "/requirement-workflow/{request_id}/content",
    response_model=ArtifactNodePage,
)
def read_requirement_workflow_content(
    project_id: int,
    request_id: UUID,
    db: Db,
    user: Reader,
    cursor: str | None = Query(default=None, max_length=1000),
    page_size: int = Query(default=30, ge=1, le=100),
    node_id: UUID | None = None,
):
    """レビュー対象の範囲と選択済み添付の固定内容を表示する。"""
    return service.content(
        db, user, project_id, request_id, cursor, page_size, node_id=node_id
    )


@router.get(
    "/requirement-workflow/{request_id}/formal-approval-decision",
    response_model=RequirementFormalApprovalDecisionPreview,
)
def preview_requirement_formal_approval_decision(
    project_id: int,
    request_id: UUID,
    db: Db,
    user: Reader,
):
    """承認する固定版、操作可否、前回の基準を表示する。"""
    return formal_approval.decision_preview(db, user, project_id, request_id)


@router.post(
    "/requirement-workflow/{request_id}/formal-approval-decision",
    response_model=RequirementWorkflowRead,
)
def decide_requirement_formal_approval(
    project_id: int,
    request_id: UUID,
    data: RequirementFormalApprovalDecisionInput,
    db: Db,
    user: Reader,
):
    """指定された本人の正式承認または理由付き差し戻しを記録する。"""
    return formal_approval.decide(db, user, project_id, request_id, data)


@router.get(
    "/requirement-workflow/{request_id}/review-progress",
    response_model=RequirementReviewProgress,
)
def read_workflow_review_progress(
    project_id: int,
    request_id: UUID,
    db: Db,
    user: Reader,
    node_ids: Annotated[list[UUID] | None, Query(max_length=100)] = None,
):
    """現在の全体進捗と画面に表示する個別要件の状態を返す。"""
    return review.progress(db, user, project_id, request_id, node_ids or [])


@router.put(
    "/requirement-workflow/{request_id}/review-items/{node_id}",
    response_model=RequirementReviewProgress,
)
def put_workflow_review_item(
    project_id: int,
    request_id: UUID,
    node_id: UUID,
    data: RequirementReviewConfirmationInput,
    db: Db,
    user: Reader,
):
    """個別要件の本人確認を保存する、または取り消す。"""
    return review.set_confirmation(db, user, project_id, request_id, node_id, data)


@router.post(
    "/requirement-workflow/{request_id}/review-confirm-remaining",
    response_model=RequirementReviewProgress,
)
def confirm_remaining_workflow_review_items(
    project_id: int,
    request_id: UUID,
    data: RequirementReviewVersionInput,
    db: Db,
    user: Reader,
):
    """修正依頼中を除く残りの要件をまとめて確認する。"""
    return review.confirm_remaining(db, user, project_id, request_id, data.version)


@router.post(
    "/requirement-workflow/{request_id}/review-result",
    response_model=RequirementWorkflowRead,
)
def submit_workflow_review_result(
    project_id: int,
    request_id: UUID,
    data: RequirementReviewSubmitInput,
    db: Db,
    user: Reader,
):
    """指定された本人としてレビューの結果を提出する。"""
    return review.submit(db, user, project_id, request_id, data)


@router.get(
    "/requirement-workflow/{request_id}/review-threads",
    response_model=RequirementReviewThreadList,
)
def list_workflow_review_threads(
    project_id: int,
    request_id: UUID,
    db: Db,
    user: Reader,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    active_only: bool = False,
    node_id: UUID | None = None,
):
    """レビューに寄せられたコメントと修正依頼を取得する。"""
    return review.threads(
        db, user, project_id, request_id, page, page_size, active_only, node_id
    )


@router.post(
    "/requirement-workflow/{request_id}/review-threads",
    response_model=RequirementReviewThreadRead,
    status_code=status.HTTP_201_CREATED,
)
def create_workflow_review_thread(
    project_id: int,
    request_id: UUID,
    data: RequirementReviewThreadCreate,
    db: Db,
    user: Reader,
):
    """固定した箇所への指摘・コメントを登録する。"""
    return review.add_thread(db, user, project_id, request_id, data)


@router.get(
    "/requirement-workflow/{request_id}/review-threads/{thread_id}",
    response_model=RequirementReviewThreadDetail,
)
def read_workflow_review_thread(
    project_id: int,
    request_id: UUID,
    thread_id: UUID,
    db: Db,
    user: Reader,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
):
    """指摘と、付随する操作履歴を取得する。"""
    return review.thread(db, user, project_id, request_id, thread_id, page, page_size)


@router.post(
    "/requirement-workflow/{request_id}/review-threads/{thread_id}/actions",
    response_model=RequirementReviewThreadRead,
)
def act_on_workflow_review_thread(
    project_id: int,
    request_id: UUID,
    thread_id: UUID,
    data: RequirementReviewThreadActionInput,
    db: Db,
    user: Reader,
):
    """返信・修正への回答・再確認・解決を記録する。"""
    return review.act_on_thread(db, user, project_id, request_id, thread_id, data)
