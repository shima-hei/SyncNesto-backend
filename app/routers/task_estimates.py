"""Read and revise the estimates of authorized project tasks."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.repositories.task_estimate import estimate_methods
from app.routers.tasks_shared import task_service
from app.schemas.task_estimate import (
    TaskEstimateMethodRead,
    TaskEstimateRead,
    TaskEstimateUpdate,
)
from app.services.task_estimate import read_task_estimate, save_task_estimate

router = APIRouter(tags=["task-estimates"])


@router.get(
    "/projects/{project_id}/task-estimate-methods",
    response_model=list[TaskEstimateMethodRead],
)
def list_task_estimate_methods(
    project_id: int,
    task_ids: list[int] = Query(min_length=1, max_length=100),
    _: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
):
    return estimate_methods(db, project_id, task_ids)


@router.get("/tasks/{task_id}/estimate", response_model=TaskEstimateRead)
def get_task_estimate(
    task_id: int,
    page: int = Query(1, ge=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = task_service.get_task(db, task_id)
    task_service.ensure_user_can_access_task(
        db, user=user, task=task, permission_code="task:read"
    )
    return read_task_estimate(db, task, page)


@router.patch("/tasks/{task_id}/estimate", response_model=TaskEstimateRead)
def update_task_estimate(
    task_id: int,
    data: TaskEstimateUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = task_service.get_task(db, task_id)
    task_service.ensure_user_can_access_task(
        db, user=user, task=task, permission_code="task:update"
    )
    return save_task_estimate(db, task_id, data, user.id)
