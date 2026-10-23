"""外部への固定判断依頼と、現在許可された本人だけの判断API。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.core.auth import get_current_user
from app.models.user import User
from app.routers.artifact_releases import Creator, Db
from app.routers.guest_invitations import InvitationRoute
from app.schemas.fixed_approval import FixedApprovalRead
from app.schemas.guest_fixed_approval import (
    ApprovalStatus,
    GuestApprovalBases,
    GuestApprovalBasisInput,
    GuestApprovalCandidates,
    GuestApprovalCreate,
    GuestApprovalInput,
    GuestApprovalPreview,
    GuestApprovalTarget,
    SharedApprovalDecision,
    SharedApprovalDecisionPreview,
    SharedApprovalPage,
    SharedApprovalRead,
)
from app.services.guest_fixed_approval import GuestFixedApprovalService
from app.services.shared_fixed_approval import SharedFixedApprovalService

router = APIRouter(
    prefix="/projects/{project_id}/guest-fixed-approvals",
    tags=["guest-fixed-approvals"],
    route_class=InvitationRoute,
)
shared_router = APIRouter(
    prefix="/shared/publications",
    tags=["shared-fixed-approvals"],
    route_class=InvitationRoute,
)
SharedUser = Annotated[User, Depends(get_current_user)]
service = GuestFixedApprovalService()
shared = SharedFixedApprovalService()


@router.get("/approver-candidates", response_model=GuestApprovalCandidates)
def guest_approval_candidates(
    project_id: int,
    publication_id: UUID,
    target_node_id: UUID,
    db: Db,
    user: Creator,
    q: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """現在の共有先で、自己承認でない候補だけを検索する。"""
    return service.candidates(
        db,
        user,
        project_id,
        GuestApprovalTarget(
            publication_id=publication_id, target_node_id=target_node_id
        ),
        q,
        page,
        page_size,
    )


@router.post("/basis-preview", response_model=GuestApprovalBases)
def guest_approval_basis(
    project_id: int, data: GuestApprovalBasisInput, db: Db, user: Creator
):
    """外部文書の通常根拠と、例外理由が必要な全要件を確認する。"""
    return service.basis(db, user, project_id, data)


@router.post("/preview", response_model=GuestApprovalPreview)
def preview_guest_approval(
    project_id: int, data: GuestApprovalInput, db: Db, user: Creator
):
    """相手に見せる固定対象・依頼文・例外を確認する。"""
    return service.preview(db, user, project_id, data)


@router.post("", response_model=FixedApprovalRead, status_code=201)
def create_guest_approval(
    project_id: int,
    data: GuestApprovalCreate,
    db: Db,
    user: Creator,
    response: Response,
):
    """既存正本と不変な外部対応を同時に保存する。"""
    result, created = service.create(db, user, project_id, data)
    response.status_code = 201 if created else 200
    return result


@shared_router.get("/approval-requests", response_model=SharedApprovalPage)
def shared_approval_requests(
    db: Db,
    user: SharedUser,
    q: str = Query(default="", max_length=200),
    status: ApprovalStatus | None = None,
    publication_id: UUID | None = None,
    cursor: str | None = Query(default=None, max_length=1000),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """現在見られる、自分への判断依頼だけを一覧する。"""
    return shared.listing(db, user, q, status, cursor, page_size, publication_id)


@shared_router.get(
    "/{publication_id}/approval-requests/{approval_id}",
    response_model=SharedApprovalRead,
)
def read_shared_approval(
    publication_id: UUID, approval_id: UUID, db: Db, user: SharedUser
):
    """現在の本人資格で公開された今回の履歴だけを取得する。"""
    return shared.metadata(db, user, publication_id, approval_id)


@shared_router.get(
    "/{publication_id}/approval-requests/{approval_id}/bases",
    response_model=GuestApprovalBases,
)
def shared_approval_bases(
    publication_id: UUID,
    approval_id: UUID,
    db: Db,
    user: SharedUser,
    cursor: str | None = Query(default=None, max_length=1000),
    page_size: int = Query(default=50, ge=1, le=100),
):
    """他の人の履歴を含まない固定した合意根拠・例外を取得する。"""
    return shared.basis(db, user, publication_id, approval_id, cursor, page_size)


@shared_router.post(
    "/{publication_id}/approval-requests/{approval_id}/decision-preview",
    response_model=SharedApprovalDecisionPreview,
)
def preview_shared_approval(
    publication_id: UUID, approval_id: UUID, db: Db, user: SharedUser
):
    """現在の指定本人・固定内容・変更比較を確認する。"""
    return shared.preview(db, user, publication_id, approval_id)


@shared_router.post(
    "/{publication_id}/approval-requests/{approval_id}/decisions",
    response_model=SharedApprovalRead,
)
def decide_shared_approval(
    publication_id: UUID,
    approval_id: UUID,
    data: SharedApprovalDecision,
    db: Db,
    user: SharedUser,
):
    """自己・代理を認めず、本人の判断を一度だけ確定する。"""
    return shared.decide(db, user, publication_id, approval_id, data)
