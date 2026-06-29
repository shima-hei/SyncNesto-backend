"""タスクマイルストーンAPIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.routers.tasks_shared import (
    task_milestone_service,
    task_presenter,
    task_service,
)
from app.schemas.task import MilestoneCreate, MilestoneRead, MilestoneUpdate

router = APIRouter(tags=["tasks"])


@router.get(
    "/projects/{project_id}/milestones",
    response_model=list[MilestoneRead],
)
def list_milestones(
    project_id: int,
    _: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
) -> list[MilestoneRead]:
    """プロジェクト内マイルストーン一覧を取得する。"""
    return task_presenter.build_milestone_responses(
        task_milestone_service.list_milestones(db, project_id)
    )


@router.post(
    "/projects/{project_id}/milestones",
    response_model=MilestoneRead,
    status_code=status.HTTP_201_CREATED,
)
def create_milestone(
    project_id: int,
    milestone_in: MilestoneCreate,
    current_user: User = Depends(require_project_permission("task:update")),
    db: Session = Depends(get_db),
) -> MilestoneRead:
    """プロジェクト内にマイルストーンを作成する。"""
    milestone = task_milestone_service.create_milestone(
        db,
        project_id=project_id,
        milestone_in=milestone_in,
        actor_id=current_user.id,
    )
    return task_presenter.build_milestone_response(milestone)


@router.get("/milestones/{milestone_id}", response_model=MilestoneRead)
def read_milestone(
    milestone_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MilestoneRead:
    """マイルストーン詳細を取得する。"""
    milestone = task_milestone_service.get_milestone(db, milestone_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=milestone.project_id,
        permission_code="task:read",
    )
    return task_presenter.build_milestone_response(milestone)


@router.patch("/milestones/{milestone_id}", response_model=MilestoneRead)
def update_milestone(
    milestone_id: int,
    milestone_in: MilestoneUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MilestoneRead:
    """マイルストーンを更新する。"""
    milestone = task_milestone_service.get_milestone(db, milestone_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=milestone.project_id,
        permission_code="task:update",
    )
    milestone = task_milestone_service.update_milestone(
        db,
        milestone_id=milestone_id,
        milestone_in=milestone_in,
        actor_id=current_user.id,
    )
    return task_presenter.build_milestone_response(milestone)


@router.delete("/milestones/{milestone_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_milestone(
    milestone_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """マイルストーンを論理削除する。"""
    milestone = task_milestone_service.get_milestone(db, milestone_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=milestone.project_id,
        permission_code="task:update",
    )
    task_milestone_service.delete_milestone(
        db,
        milestone_id=milestone_id,
        actor_id=current_user.id,
    )
