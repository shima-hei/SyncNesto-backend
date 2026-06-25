"""要件APIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.requirement import Requirement
from app.models.user import User
from app.routers import requirements_shared as shared
from app.schemas.requirement import (
    RequirementCreate,
    RequirementDetailRead,
    RequirementLinkRead,
    RequirementListResponse,
    RequirementRead,
    RequirementReviewRead,
    RequirementRevisionRead,
    RequirementSummaryRead,
    RequirementUpdate,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["requirements"])


@router.post(
    "/requirements",
    response_model=RequirementRead,
    status_code=status.HTTP_201_CREATED,
)
def create_requirement(
    project_id: int,
    requirement_in: RequirementCreate,
    current_user: User = Depends(require_project_permission("requirement:create")),
    db: Session = Depends(get_db),
) -> Requirement:
    """要件を作成する。

    Args:
        project_id: 作成対象のプロジェクトID。
        requirement_in: 要件の作成入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        作成された要件。
    """
    return shared.requirement_service.create_requirement(
        db,
        project_id=project_id,
        requirement_in=requirement_in,
        actor_id=current_user.id,
    )


@router.get(
    "/requirements",
    response_model=RequirementListResponse,
)
def list_requirements(
    project_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    document_id: int | None = Query(default=None),
    section_id: int | None = Query(default=None),
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    requirement_type: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    owner_id: int | None = Query(default=None),
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> RequirementListResponse:
    """要件一覧を取得する。

    Args:
        project_id: 一覧取得対象のプロジェクトID。
        page: 取得ページ番号。
        page_size: 1ページあたりの取得件数。
        document_id: 絞り込み対象の要件定義書ID。
        section_id: 絞り込み対象の要件定義セクションID。
        q: 検索キーワード。
        status: 絞り込み対象のステータス。
        requirement_type: 絞り込み対象の要件種別。
        priority: 絞り込み対象の優先度。
        owner_id: 絞り込み対象のオーナーID。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        要件のページング済み一覧。
    """
    requirements, total = shared.requirement_service.list_requirements_paginated(
        db,
        project_id=project_id,
        page=page,
        page_size=page_size,
        document_id=document_id,
        section_id=section_id,
        q=q,
        status=status,
        requirement_type=requirement_type,
        priority=priority,
        owner_id=owner_id,
    )
    return RequirementListResponse(
        items=[
            RequirementRead.model_validate(requirement) for requirement in requirements
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/requirements/{requirement_id}",
    response_model=RequirementRead,
)
def read_requirement(
    project_id: int,
    requirement_id: int,
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> Requirement:
    """要件を取得する。

    Args:
        project_id: 取得対象のプロジェクトID。
        requirement_id: 取得対象の要件ID。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        取得した要件。
    """
    return shared.requirement_service.get_requirement(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
    )


@router.get(
    "/requirements/{requirement_id}/summary",
    response_model=RequirementSummaryRead,
)
def read_requirement_summary(
    project_id: int,
    requirement_id: int,
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> RequirementSummaryRead:
    """要件詳細画面用の集約情報を取得する。

    Args:
        project_id: 取得対象のプロジェクトID。
        requirement_id: 取得対象の要件ID。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        要件詳細画面用の集約情報。
    """
    summary = shared.requirement_child_service.get_summary(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
    )
    return RequirementSummaryRead(
        requirement=RequirementRead.model_validate(summary["requirement"]),
        details=[
            RequirementDetailRead.model_validate(detail)
            for detail in summary["details"]
        ],
        links=[RequirementLinkRead.model_validate(link) for link in summary["links"]],
        comments=shared.requirement_child_service.build_comment_reads(
            db,
            summary["comments"],
        ),
        reviews=[
            RequirementReviewRead.model_validate(review)
            for review in summary["reviews"]
        ],
        revisions=[
            RequirementRevisionRead.model_validate(revision)
            for revision in summary["revisions"]
        ],
    )


@router.patch(
    "/requirements/{requirement_id}",
    response_model=RequirementRead,
)
def update_requirement(
    project_id: int,
    requirement_id: int,
    requirement_in: RequirementUpdate,
    current_user: User = Depends(require_project_permission("requirement:update")),
    db: Session = Depends(get_db),
) -> Requirement:
    """要件を更新する。

    Args:
        project_id: 更新対象のプロジェクトID。
        requirement_id: 更新対象の要件ID。
        requirement_in: 要件の更新入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        更新された要件。
    """
    return shared.requirement_service.update_requirement(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        requirement_in=requirement_in,
        actor_id=current_user.id,
    )


@router.delete(
    "/requirements/{requirement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_requirement(
    project_id: int,
    requirement_id: int,
    current_user: User = Depends(require_project_permission("requirement:delete")),
    db: Session = Depends(get_db),
) -> None:
    """要件を論理削除する。

    Args:
        project_id: 削除対象のプロジェクトID。
        requirement_id: 削除対象の要件ID。
        current_user: 認証済みユーザー。
        db: DBセッション。
    """
    shared.requirement_service.delete_requirement(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        actor_id=current_user.id,
    )


@router.get(
    "/requirements/{requirement_id}/revisions",
    response_model=list[RequirementRevisionRead],
)
def list_requirement_revisions(
    project_id: int,
    requirement_id: int,
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> list[RequirementRevisionRead]:
    """要件の改訂履歴一覧を取得する。

    Args:
        project_id: 取得対象のプロジェクトID。
        requirement_id: 改訂履歴を取得する要件ID。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        要件の改訂履歴一覧。
    """
    revisions = shared.requirement_service.list_revisions(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
    )
    return [RequirementRevisionRead.model_validate(revision) for revision in revisions]
