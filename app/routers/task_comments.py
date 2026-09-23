"""タスクコメントAPIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.routers.tasks_shared import (
    task_comment_service,
    task_presenter,
    task_service,
)
from app.schemas.task import (
    TaskCommentCreate,
    TaskCommentRead,
    TaskCommentStateUpdate,
    TaskCommentUpdate,
)

router = APIRouter(tags=["tasks"])


@router.get("/tasks/{task_id}/comments", response_model=list[TaskCommentRead])
def list_task_comments(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TaskCommentRead]:
    """タスクコメント一覧を取得する。"""
    task = task_service.get_task(db, task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:read",
    )
    comments = task_comment_service.list_comments(db, task_id)
    return task_presenter.build_task_comment_responses(
        comments,
        users_by_id=task_comment_service.get_task_comment_users_by_id(db, comments),
    )


@router.post(
    "/tasks/{task_id}/comments",
    response_model=TaskCommentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_task_comment(
    task_id: int,
    comment_in: TaskCommentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskCommentRead:
    """タスクコメントを作成する。"""
    task = task_service.get_task(db, task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:comment",
    )
    comment = task_comment_service.create_comment(
        db,
        task_id=task_id,
        comment_in=comment_in,
        actor_id=current_user.id,
    )
    return task_presenter.build_task_comment_response(comment, user=current_user)


@router.patch("/task-comments/{comment_id}", response_model=TaskCommentRead)
def update_task_comment(
    comment_id: int,
    comment_in: TaskCommentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskCommentRead:
    """タスクコメントを更新する。"""
    comment = task_comment_service.get_comment(db, comment_id)
    task = task_service.get_task(db, comment.task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:comment",
    )
    comment = task_comment_service.update_comment(
        db,
        comment_id=comment_id,
        comment_in=comment_in,
        actor_id=current_user.id,
    )
    return task_presenter.build_task_comment_response(comment, user=current_user)


@router.delete("/task-comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task_comment(
    comment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """タスクコメントを論理削除する。"""
    comment = task_comment_service.get_comment(db, comment_id)
    task = task_service.get_task(db, comment.task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:comment",
    )
    task_comment_service.delete_comment(
        db,
        comment_id=comment_id,
        actor_id=current_user.id,
    )


@router.post("/task-comments/{comment_id}/resolve", response_model=TaskCommentRead)
def resolve_task_comment(
    comment_id: int,
    comment_in: TaskCommentStateUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskCommentRead:
    """タスクコメントを解決済みにする。"""
    comment = task_comment_service.get_comment(db, comment_id)
    task = task_service.get_task(db, comment.task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:comment",
    )
    comment = task_comment_service.resolve_comment(
        db,
        comment_id=comment_id,
        comment_in=comment_in,
        actor_id=current_user.id,
    )
    return task_presenter.build_task_comment_response(comment, user=current_user)


@router.post("/task-comments/{comment_id}/reopen", response_model=TaskCommentRead)
def reopen_task_comment(
    comment_id: int,
    comment_in: TaskCommentStateUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskCommentRead:
    """タスクコメントを未解決に戻す。"""
    comment = task_comment_service.get_comment(db, comment_id)
    task = task_service.get_task(db, comment.task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:comment",
    )
    comment = task_comment_service.reopen_comment(
        db,
        comment_id=comment_id,
        comment_in=comment_in,
        actor_id=current_user.id,
    )
    return task_presenter.build_task_comment_response(comment, user=current_user)
