"""タスク依存関係APIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.routers.tasks_shared import (
    task_dependency_service,
    task_presenter,
    task_service,
)
from app.schemas.task import (
    TaskDependencyCreate,
    TaskDependencyRead,
    TaskDependencyUpdate,
)

router = APIRouter(tags=["tasks"])


@router.get("/tasks/{task_id}/dependencies", response_model=list[TaskDependencyRead])
def list_task_dependencies(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TaskDependencyRead]:
    """タスク依存関係一覧を取得する。"""
    task = task_service.get_task(db, task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:read",
    )
    return task_presenter.build_task_dependency_responses(
        task_dependency_service.list_dependencies(db, task_id)
    )


@router.post(
    "/tasks/{task_id}/dependencies",
    response_model=TaskDependencyRead,
    status_code=status.HTTP_201_CREATED,
)
def create_task_dependency(
    task_id: int,
    dependency_in: TaskDependencyCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskDependencyRead:
    """タスク依存関係を作成する。"""
    task = task_service.get_task(db, task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:update",
    )
    if task_id != dependency_in.successor_task_id:
        dependency_in = dependency_in.model_copy(update={"successor_task_id": task_id})
    dependency = task_dependency_service.create_dependency(
        db,
        dependency_in=dependency_in,
        actor_id=current_user.id,
    )
    return task_presenter.build_task_dependency_response(dependency)


@router.patch(
    "/task-dependencies/{dependency_id}",
    response_model=TaskDependencyRead,
)
def update_task_dependency(
    dependency_id: int,
    dependency_in: TaskDependencyUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskDependencyRead:
    """タスク依存関係を更新する。"""
    dependency = task_dependency_service.get_dependency(db, dependency_id)
    task = task_service.get_task(db, dependency.successor_task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:update",
    )
    dependency = task_dependency_service.update_dependency(
        db,
        dependency_id=dependency_id,
        dependency_in=dependency_in,
        actor_id=current_user.id,
    )
    return task_presenter.build_task_dependency_response(dependency)


@router.delete(
    "/task-dependencies/{dependency_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_task_dependency(
    dependency_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """タスク依存関係を削除する。"""
    dependency = task_dependency_service.get_dependency(db, dependency_id)
    task = task_service.get_task(db, dependency.successor_task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:update",
    )
    task_dependency_service.delete_dependency(
        db,
        dependency_id=dependency_id,
        actor_id=current_user.id,
    )
