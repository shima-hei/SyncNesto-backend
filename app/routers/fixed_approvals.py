"""固定内容への内部承認、例外根拠、旧履歴移行のAPI。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.core.auth import require_project_permission
from app.models.user import User
from app.routers.artifact_releases import Creator, Db
from app.schemas.fixed_approval import (
    ApprovalBasisPage,
    ApprovalCancelInput,
    ApprovalCandidates,
    ApprovalDecisionInput,
    ApprovalDecisionPreview,
    FixedApprovalCreate,
    FixedApprovalHistoryPage,
    FixedApprovalPreview,
    FixedApprovalRead,
    FixedApprovalScopeInput,
    LegacyApprovalCreate,
    LegacyApprovalImportRead,
    LegacyApprovalPreview,
    LegacyApprovalPreviewInput,
)
from app.schemas.fixed_approval_notification import ApprovalNextActions
from app.services.approval_next_actions import ApprovalNextActionsService
from app.services.fixed_approval import FixedApprovalService
from app.services.fixed_approval_history import FixedApprovalHistoryService
from app.services.fixed_approval_migration import FixedApprovalMigrationService

router = APIRouter(prefix="/projects/{project_id}", tags=["fixed-approvals"])
Reader = Annotated[User, Depends(require_project_permission("requirement:read"))]
Decider = Annotated[User, Depends(require_project_permission("requirement:approve"))]
service = FixedApprovalService()
history = FixedApprovalHistoryService()
migration = FixedApprovalMigrationService()


@router.get(
    "/fixed-approvals/{approval_id}/next-actions", response_model=ApprovalNextActions
)
def read_approval_next_actions(
    project_id: int, approval_id: UUID, db: Db, user: Reader, response: Response
):
    """現在の権限で利用できる既存画面への次操作を返す。"""
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return ApprovalNextActionsService().read(db, user, project_id, approval_id)


@router.post("/fixed-approvals/preview", response_model=FixedApprovalPreview)
def preview_fixed_approval(
    project_id: int, data: FixedApprovalScopeInput, db: Db, user: Creator
):
    """指定先・固定内容・例外理由を保存前に確認する。"""
    return service.preview(db, user, project_id, data)


@router.post("/fixed-approvals", response_model=FixedApprovalRead, status_code=201)
def create_fixed_approval(
    project_id: int,
    data: FixedApprovalCreate,
    db: Db,
    user: Creator,
    response: Response,
):
    """確認した内容への依頼だけを冪等に保存する。"""
    result, created = service.create(db, user, project_id, data)
    response.status_code = 201 if created else 200
    return result


@router.get("/fixed-approvals", response_model=FixedApprovalHistoryPage)
def list_fixed_approvals(
    project_id: int,
    db: Db,
    user: Reader,
    target_kind: str | None = Query(default=None, max_length=30),
    source_id: int | None = Query(default=None, gt=0),
    status: str | None = Query(default=None, max_length=30),
    mine: bool = False,
    scheme: Literal["fixed", "legacy"] | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    audience: Literal["internal", "guest"] | None = None,
    publication_id: UUID | None = None,
):
    """新旧の依頼を、許可された集合と件数だけで一覧する。"""
    return history.list(
        db,
        user,
        project_id,
        target_kind,
        source_id,
        status,
        mine,
        scheme,
        page,
        page_size,
        audience,
        publication_id,
    )


@router.get("/fixed-approvals/basis", response_model=ApprovalBasisPage)
def read_fixed_approval_basis(
    project_id: int,
    db: Db,
    user: Reader,
    release_id: UUID | None = None,
    target_node_id: UUID | None = None,
    approval_id: UUID | None = None,
    cursor: str | None = Query(default=None, max_length=1000),
    page_size: int = Query(default=50, ge=1, le=100),
):
    """個別合意の根拠と例外を、集合hash付きのページで取得する。"""
    return service.basis(
        db, user, project_id, release_id, target_node_id, approval_id, cursor, page_size
    )


@router.get("/fixed-approvals/approver-candidates", response_model=ApprovalCandidates)
def list_fixed_approval_candidates(
    project_id: int,
    release_id: UUID,
    db: Db,
    user: Creator,
    preservation_id: UUID | None = None,
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """全対象を読めて判断できる、別の本人だけを候補にする。"""
    return service.candidates(
        db, user, project_id, release_id, preservation_id, q, page, page_size
    )


@router.get("/fixed-approvals/{approval_id}", response_model=FixedApprovalRead)
def read_fixed_approval(project_id: int, approval_id: UUID, db: Db, user: Reader):
    """固定した内容と、実actorを持つ業務履歴を取得する。"""
    return service.get(db, user, project_id, approval_id)


@router.post(
    "/fixed-approvals/{approval_id}/decision-preview",
    response_model=ApprovalDecisionPreview,
)
def preview_fixed_approval_decision(
    project_id: int, approval_id: UUID, db: Db, user: Decider
):
    """指定本人として、判断対象と現在内容との差を確認する。"""
    return service.decision_preview(db, user, project_id, approval_id)


@router.post(
    "/fixed-approvals/{approval_id}/decisions", response_model=FixedApprovalRead
)
def decide_fixed_approval(
    project_id: int,
    approval_id: UUID,
    data: ApprovalDecisionInput,
    db: Db,
    user: Decider,
):
    """自己・代理を認めず、固定した内容への判断を保存する。"""
    return service.decide(db, user, project_id, approval_id, data)


@router.post("/fixed-approvals/{approval_id}/cancel", response_model=FixedApprovalRead)
def cancel_fixed_approval(
    project_id: int, approval_id: UUID, data: ApprovalCancelInput, db: Db, user: Creator
):
    """未判断の依頼を、理由付きで取消す。"""
    return service.cancel(db, user, project_id, approval_id, data)


@router.post("/approval-migrations/preview", response_model=LegacyApprovalPreview)
def preview_legacy_approval_migration(
    project_id: int, data: LegacyApprovalPreviewInput, db: Db, user: Creator
):
    """旧記録の実操作者と、対象本文が証明できるかを確認する。"""
    return migration.preview(db, user, project_id, data)


@router.post(
    "/approval-migrations", response_model=LegacyApprovalImportRead, status_code=201
)
def migrate_legacy_approval(
    project_id: int,
    data: LegacyApprovalCreate,
    db: Db,
    user: Creator,
    response: Response,
):
    """選択した旧記録を取り込み、必要なら未判断依頼を一括切替する。"""
    result, created = migration.create(db, user, project_id, data)
    response.status_code = 201 if created else 200
    return result


@router.get("/approval-migrations/{import_id}", response_model=LegacyApprovalImportRead)
def read_legacy_approval_import(
    project_id: int,
    import_id: UUID,
    db: Db,
    user: Reader,
    cursor: str | None = Query(default=None, max_length=1000),
):
    """取り込んだ証拠を元対象の削除後もページ取得する。"""
    return migration.get(db, user, project_id, import_id, cursor)
