"""要件レビューの公開依頼と、指定本人の確認・指摘。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.core.auth import get_current_user
from app.models.user import User
from app.routers.artifact_releases import Db
from app.routers.guest_invitations import InvitationRoute
from app.schemas.guest_requirement_workflow import (
    GuestRequirementFormalApprovalCreate,
    GuestRequirementFormalApprovalInput,
    GuestRequirementReviewCreate,
    GuestRequirementReviewOptions,
    SharedRequirementApprovalComparison,
    SharedRequirementFormalApprovalDecision,
    SharedRequirementFormalApprovalRead,
    SharedRequirementReviewList,
    SharedRequirementReviewNodes,
    SharedRequirementReviewRead,
    SharedRequirementReviewReplies,
    SharedRequirementReviewThread,
    SharedRequirementReviewThreads,
)
from app.schemas.requirement_workflow import (
    RequirementFormalApprovalPreview,
    RequirementReviewConfirmationInput,
    RequirementReviewSubmitInput,
    RequirementReviewThreadActionInput,
    RequirementReviewThreadCreate,
    RequirementWorkflowRead,
)
from app.services.guest_requirement_workflow import GuestRequirementWorkflowService

router = APIRouter(
    prefix="/projects/{project_id}/guest-requirement-reviews",
    tags=["guest-requirement-reviews"],
    route_class=InvitationRoute,
)
approval_router = APIRouter(
    prefix="/projects/{project_id}/guest-requirement-approvals",
    tags=["guest-requirement-approvals"],
    route_class=InvitationRoute,
)
shared_router = APIRouter(
    prefix="/shared/publications",
    tags=["shared-requirement-reviews"],
    route_class=InvitationRoute,
)
Actor = Annotated[User, Depends(get_current_user)]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=30)]
TARGET = "/{publication_id}/requirement-reviews/{request_id}"
APPROVAL_TARGET = "/{publication_id}/requirement-approvals/{request_id}"
service = GuestRequirementWorkflowService()


@router.get("/options", response_model=GuestRequirementReviewOptions)
def guest_requirement_review_options(
    project_id: int, publication_id: UUID, db: Db, user: Actor
):
    """公開版で現在有効なレビュー担当候補を取得する。"""
    return service.options(db, user, project_id, publication_id)


@router.post("", response_model=RequirementWorkflowRead, status_code=201)
def create_guest_requirement_review(
    project_id: int,
    data: GuestRequirementReviewCreate,
    response: Response,
    db: Db,
    user: Actor,
):
    """公開された固定内容と相手ごとの許可に結び付けて依頼する。"""
    item, created = service.create(db, user, project_id, data)
    response.status_code = 201 if created else 200
    return item


@shared_router.get("/requirement-reviews", response_model=SharedRequirementReviewList)
def list_shared_requirement_reviews(
    db: Db,
    user: Actor,
    publication_id: UUID | None = None,
    page: Page = 1,
    page_size: PageSize = 20,
):
    """有効な許可で本人に届いたレビューを一覧する。"""
    return service.listing(db, user, publication_id, page, page_size)


@shared_router.get(TARGET, response_model=SharedRequirementReviewRead)
def read_shared_requirement_review(
    publication_id: UUID, request_id: UUID, db: Db, user: Actor
):
    """現在の本人確認状況と固定対象を取得する。"""
    return service.metadata(db, user, publication_id, request_id)


@shared_router.get(TARGET + "/nodes", response_model=SharedRequirementReviewNodes)
def read_shared_requirement_review_nodes(
    publication_id: UUID,
    request_id: UUID,
    db: Db,
    user: Actor,
    cursor: Annotated[str | None, Query(max_length=1000)] = None,
    page_size: PageSize = 30,
):
    """指定された範囲の公開DTOと個別確認を取得する。"""
    return service.nodes(db, user, publication_id, request_id, cursor, page_size)


@shared_router.put(
    TARGET + "/nodes/{node_id}", response_model=SharedRequirementReviewRead
)
def confirm_shared_requirement_review_node(
    publication_id: UUID,
    request_id: UUID,
    node_id: UUID,
    data: RequirementReviewConfirmationInput,
    db: Db,
    user: Actor,
):
    """公開要件を本人が確認または取消する。"""
    return service.confirm(db, user, publication_id, request_id, node_id, data)


@shared_router.post(TARGET + "/submission", response_model=SharedRequirementReviewRead)
def submit_shared_requirement_review(
    publication_id: UUID,
    request_id: UUID,
    data: RequirementReviewSubmitInput,
    db: Db,
    user: Actor,
):
    """本人の結果を提出し、全担当の回答を集計する。"""
    return service.submit(db, user, publication_id, request_id, data)


@shared_router.get(TARGET + "/threads", response_model=SharedRequirementReviewThreads)
def list_shared_requirement_review_threads(
    publication_id: UUID,
    request_id: UUID,
    node_id: UUID,
    db: Db,
    user: Actor,
    page: Page = 1,
    page_size: PageSize = 10,
):
    """指定箇所に本人が投稿した指摘を取得する。"""
    return service.threads(
        db, user, publication_id, request_id, node_id, page, page_size
    )


@shared_router.post(
    TARGET + "/threads", response_model=SharedRequirementReviewThread, status_code=201
)
def add_shared_requirement_review_thread(
    publication_id: UUID,
    request_id: UUID,
    data: RequirementReviewThreadCreate,
    db: Db,
    user: Actor,
):
    """公開箇所へ本人のコメントまたは修正指摘を登録する。"""
    return service.add_thread(db, user, publication_id, request_id, data)


@shared_router.get(
    TARGET + "/threads/{thread_id}/replies",
    response_model=SharedRequirementReviewReplies,
)
def read_shared_requirement_review_replies(
    publication_id: UUID,
    request_id: UUID,
    thread_id: UUID,
    db: Db,
    user: Actor,
    page: Page = 1,
    page_size: PageSize = 20,
):
    """本人の指摘への返信をページ取得する。"""
    return service.replies(
        db, user, publication_id, request_id, thread_id, page, page_size
    )


@shared_router.post(
    TARGET + "/threads/{thread_id}/actions",
    response_model=SharedRequirementReviewThread,
)
def act_on_shared_requirement_review_thread(
    publication_id: UUID,
    request_id: UUID,
    thread_id: UUID,
    data: RequirementReviewThreadActionInput,
    db: Db,
    user: Actor,
):
    """本人の指摘に返信・再確認を記録する。"""
    return service.act_on_thread(db, user, publication_id, request_id, thread_id, data)


@approval_router.post("/preview", response_model=RequirementFormalApprovalPreview)
def preview_guest_requirement_approval(
    project_id: int, data: GuestRequirementFormalApprovalInput, db: Db, user: Actor
):
    """現在の公開本文・承認者・承認条件を確認する。"""
    return service.formal_preview(db, user, project_id, data)


@approval_router.post("", response_model=RequirementWorkflowRead, status_code=201)
def create_guest_requirement_approval(
    project_id: int,
    data: GuestRequirementFormalApprovalCreate,
    response: Response,
    db: Db,
    user: Actor,
):
    """社内と外部の判断を、一件の文書全体の正式承認へ結び付ける。"""
    item, created = service.create_formal(db, user, project_id, data)
    response.status_code = 201 if created else 200
    return item


@shared_router.get("/requirement-approvals", response_model=SharedRequirementReviewList)
def list_shared_requirement_approvals(
    db: Db,
    user: Actor,
    publication_id: UUID | None = None,
    page: Page = 1,
    page_size: PageSize = 20,
):
    """有効な許可で本人に届いた正式承認を一覧する。"""
    return service.listing(
        db, user, publication_id, page, page_size, kind="formal_approval"
    )


@shared_router.get(APPROVAL_TARGET, response_model=SharedRequirementFormalApprovalRead)
def read_shared_requirement_approval(
    publication_id: UUID, request_id: UUID, db: Db, user: Actor
):
    """本人の正式承認と現在閲覧できる承認済みの比較版を取得する。"""
    return service.formal_metadata(db, user, publication_id, request_id)


@shared_router.get(
    APPROVAL_TARGET + "/comparison", response_model=SharedRequirementApprovalComparison
)
def read_shared_requirement_approval_comparison(
    publication_id: UUID,
    request_id: UUID,
    db: Db,
    user: Actor,
    page: Page = 1,
    page_size: PageSize = 20,
):
    """承認対象の公開版と本人が閲覧できる前回承認版の差分を返す。"""
    return service.formal_comparison(
        db, user, publication_id, request_id, page, page_size
    )


@shared_router.post(
    APPROVAL_TARGET + "/decision", response_model=SharedRequirementFormalApprovalRead
)
def decide_shared_requirement_approval(
    publication_id: UUID,
    request_id: UUID,
    data: SharedRequirementFormalApprovalDecision,
    db: Db,
    user: Actor,
):
    """本人の承認・差し戻しを不変の文書全体に記録する。"""
    return service.decide_formal(db, user, publication_id, request_id, data)
