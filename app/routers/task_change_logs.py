"""タスク変更履歴APIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.routers.tasks_shared import (
    task_change_log_service,
    task_presenter,
    task_service,
)
from app.schemas.task import TaskChangeLogListResponse

router = APIRouter(tags=["tasks"])


@router.get(
    "/tasks/{task_id}/change-logs",
    response_model=TaskChangeLogListResponse,
)
def list_task_change_logs(
    task_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskChangeLogListResponse:
    """タスク変更履歴一覧を取得する。"""
    task = task_service.get_task(db, task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:read",
    )
    change_logs, total = task_change_log_service.list_task_change_logs(
        db,
        task_id=task_id,
        page=page,
        page_size=page_size,
    )
    presentation_data = task_change_log_service.get_task_change_log_presentation_data(
        db,
        change_logs,
    )
    return task_presenter.build_task_change_log_list_response(
        change_logs=change_logs,
        total=total,
        page=page,
        page_size=page_size,
        presentation_data=presentation_data,
    )
