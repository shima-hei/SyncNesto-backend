"""要件定義対象コメントAPIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.requirement import RequirementTargetComment
from app.models.user import User
from app.routers import requirements_shared as shared
from app.schemas.requirement import (
    RequirementTargetCommentCreate,
    RequirementTargetCommentRead,
    RequirementTargetCommentStateUpdate,
    RequirementTargetCommentUpdate,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["requirements"])


@router.post(
    "/comments",
    response_model=RequirementTargetCommentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_target_comment(
    project_id: int,
    comment_in: RequirementTargetCommentCreate,
    current_user: User = Depends(require_project_permission("requirement:comment")),
    db: Session = Depends(get_db),
) -> RequirementTargetComment:
    """要件定義対象コメントを作成する。

    Args:
        project_id: 作成対象のプロジェクトID。
        comment_in: コメント作成入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        作成された要件定義対象コメント。
    """
    return shared.target_comment_service.create_comment(
        db,
        project_id=project_id,
        comment_in=comment_in,
        author_id=current_user.id,
    )


@router.get(
    "/comments",
    response_model=list[RequirementTargetCommentRead],
)
def list_target_comments(
    project_id: int,
    target_type: str = Query(),
    target_id: int = Query(),
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> list[RequirementTargetComment]:
    """要件定義対象コメント一覧を取得する。

    Args:
        project_id: 取得対象のプロジェクトID。
        target_type: コメント対象種別。
        target_id: コメント対象ID。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        要件定義対象コメント一覧。
    """
    return shared.target_comment_service.list_comments(
        db,
        project_id=project_id,
        target_type=target_type,
        target_id=target_id,
    )


@router.patch(
    "/comments/{comment_id}",
    response_model=RequirementTargetCommentRead,
)
def update_target_comment(
    project_id: int,
    comment_id: int,
    comment_in: RequirementTargetCommentUpdate,
    current_user: User = Depends(require_project_permission("requirement:comment")),
    db: Session = Depends(get_db),
) -> RequirementTargetComment:
    """要件定義対象コメントを更新する。

    Args:
        project_id: 更新対象のプロジェクトID。
        comment_id: 更新対象のコメントID。
        comment_in: コメント更新入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        更新された要件定義対象コメント。
    """
    return shared.target_comment_service.update_comment(
        db,
        project_id=project_id,
        comment_id=comment_id,
        comment_in=comment_in,
        actor_id=current_user.id,
    )


@router.delete(
    "/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_target_comment(
    project_id: int,
    comment_id: int,
    current_user: User = Depends(require_project_permission("requirement:comment")),
    db: Session = Depends(get_db),
) -> None:
    """要件定義対象コメントを論理削除する。

    Args:
        project_id: 削除対象のプロジェクトID。
        comment_id: 削除対象のコメントID。
        current_user: 認証済みユーザー。
        db: DBセッション。
    """
    shared.target_comment_service.delete_comment(
        db,
        project_id=project_id,
        comment_id=comment_id,
        actor_id=current_user.id,
    )


@router.post(
    "/comments/{comment_id}/resolve",
    response_model=RequirementTargetCommentRead,
)
def resolve_target_comment(
    project_id: int,
    comment_id: int,
    state_in: RequirementTargetCommentStateUpdate,
    current_user: User = Depends(require_project_permission("requirement:comment")),
    db: Session = Depends(get_db),
) -> RequirementTargetComment:
    """要件定義対象コメントを解決済みにする。

    Args:
        project_id: 更新対象のプロジェクトID。
        comment_id: 更新対象のコメントID。
        state_in: 状態更新入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        更新された要件定義対象コメント。
    """
    return shared.target_comment_service.resolve_comment(
        db,
        project_id=project_id,
        comment_id=comment_id,
        state_in=state_in,
        actor_id=current_user.id,
    )


@router.post(
    "/comments/{comment_id}/reopen",
    response_model=RequirementTargetCommentRead,
)
def reopen_target_comment(
    project_id: int,
    comment_id: int,
    state_in: RequirementTargetCommentStateUpdate,
    current_user: User = Depends(require_project_permission("requirement:comment")),
    db: Session = Depends(get_db),
) -> RequirementTargetComment:
    """要件定義対象コメントを未解決に戻す。

    Args:
        project_id: 更新対象のプロジェクトID。
        comment_id: 更新対象のコメントID。
        state_in: 状態更新入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        更新された要件定義対象コメント。
    """
    return shared.target_comment_service.reopen_comment(
        db,
        project_id=project_id,
        comment_id=comment_id,
        state_in=state_in,
        actor_id=current_user.id,
    )
