"""要件定義未決事項APIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.requirement import Requirement, RequirementOpenIssue
from app.models.user import User
from app.routers import requirements_shared as shared
from app.schemas.requirement import (
    RequirementOpenIssueCreate,
    RequirementOpenIssueListResponse,
    RequirementOpenIssuePromoteCreate,
    RequirementOpenIssueRead,
    RequirementOpenIssueUpdate,
    RequirementRead,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["requirements"])


@router.post(
    "/open-issues",
    response_model=RequirementOpenIssueRead,
    status_code=status.HTTP_201_CREATED,
)
def create_open_issue(
    project_id: int,
    issue_in: RequirementOpenIssueCreate,
    current_user: User = Depends(require_project_permission("requirement:create")),
    db: Session = Depends(get_db),
) -> RequirementOpenIssue:
    """未決事項を作成する。

    Args:
        project_id: 作成対象のプロジェクトID。
        issue_in: 未決事項作成入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        作成された未決事項。
    """
    return shared.open_issue_service.create_open_issue(
        db,
        project_id=project_id,
        issue_in=issue_in,
        actor_id=current_user.id,
    )


@router.get(
    "/open-issues",
    response_model=RequirementOpenIssueListResponse,
)
def list_open_issues(
    project_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    document_id: int | None = Query(default=None),
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    assignee_id: int | None = Query(default=None),
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> RequirementOpenIssueListResponse:
    """未決事項一覧を取得する。

    Args:
        project_id: 一覧取得対象のプロジェクトID。
        page: 取得ページ番号。
        page_size: 1ページあたりの取得件数。
        document_id: 絞り込み対象の要件定義書ID。
        q: 検索キーワード。
        status: 絞り込み対象のステータス。
        assignee_id: 絞り込み対象の担当者ID。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        未決事項のページング済み一覧。
    """
    issues, total = shared.open_issue_service.list_open_issues_paginated(
        db,
        project_id=project_id,
        page=page,
        page_size=page_size,
        document_id=document_id,
        q=q,
        status=status,
        assignee_id=assignee_id,
    )
    return RequirementOpenIssueListResponse(
        items=[RequirementOpenIssueRead.model_validate(issue) for issue in issues],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/open-issues/{issue_id}",
    response_model=RequirementOpenIssueRead,
)
def read_open_issue(
    project_id: int,
    issue_id: int,
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> RequirementOpenIssue:
    """未決事項を取得する。

    Args:
        project_id: 取得対象のプロジェクトID。
        issue_id: 取得対象の未決事項ID。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        取得した未決事項。
    """
    return shared.open_issue_service.get_open_issue(
        db,
        project_id=project_id,
        issue_id=issue_id,
    )


@router.patch(
    "/open-issues/{issue_id}",
    response_model=RequirementOpenIssueRead,
)
def update_open_issue(
    project_id: int,
    issue_id: int,
    issue_in: RequirementOpenIssueUpdate,
    current_user: User = Depends(require_project_permission("requirement:update")),
    db: Session = Depends(get_db),
) -> RequirementOpenIssue:
    """未決事項を更新する。

    Args:
        project_id: 更新対象のプロジェクトID。
        issue_id: 更新対象の未決事項ID。
        issue_in: 未決事項更新入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        更新された未決事項。
    """
    return shared.open_issue_service.update_open_issue(
        db,
        project_id=project_id,
        issue_id=issue_id,
        issue_in=issue_in,
        actor_id=current_user.id,
    )


@router.delete(
    "/open-issues/{issue_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_open_issue(
    project_id: int,
    issue_id: int,
    current_user: User = Depends(require_project_permission("requirement:delete")),
    db: Session = Depends(get_db),
) -> None:
    """未決事項を論理削除する。

    Args:
        project_id: 削除対象のプロジェクトID。
        issue_id: 削除対象の未決事項ID。
        current_user: 認証済みユーザー。
        db: DBセッション。
    """
    shared.open_issue_service.delete_open_issue(
        db,
        project_id=project_id,
        issue_id=issue_id,
        actor_id=current_user.id,
    )


@router.post(
    "/open-issues/{issue_id}/promote-to-requirement",
    response_model=RequirementRead,
    status_code=status.HTTP_201_CREATED,
)
def promote_open_issue_to_requirement(
    project_id: int,
    issue_id: int,
    promote_in: RequirementOpenIssuePromoteCreate,
    current_user: User = Depends(require_project_permission("requirement:create")),
    db: Session = Depends(get_db),
) -> Requirement:
    """未決事項を要件へ昇格する。

    Args:
        project_id: 対象プロジェクトID。
        issue_id: 昇格対象の未決事項ID。
        promote_in: 昇格入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        作成された要件。
    """
    return shared.open_issue_service.promote_to_requirement(
        db,
        project_id=project_id,
        issue_id=issue_id,
        promote_in=promote_in,
        actor_id=current_user.id,
    )
