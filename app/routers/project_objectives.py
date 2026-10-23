"""案件の業務目標・出典と選択履歴。"""

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.project_objective import (
    GoalSourceEventPage,
    ProjectObjectiveRead,
    ProjectObjectiveUpdate,
)
from app.services.project_objective import ProjectObjectiveService

router = APIRouter(
    prefix="/projects/{project_id}/objectives", tags=["project-objectives"]
)
service = ProjectObjectiveService()


@router.get("", response_model=ProjectObjectiveRead)
def read_project_objectives(
    project_id: int,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_project_permission("project:read")),
):
    """現在の権限で読める目標と出典だけを取得する。"""
    response.headers["Cache-Control"] = "no-store"
    return service.read(db, user, project_id)


@router.patch("", response_model=ProjectObjectiveRead)
def update_project_objectives(
    project_id: int,
    data: ProjectObjectiveUpdate,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_project_permission("project:manage_goal_source")),
):
    """本文を編集せず案件共通の参照文書を指定する。"""
    response.headers["Cache-Control"] = "no-store"
    return service.update(db, user, project_id, data)


@router.get("/events", response_model=GoalSourceEventPage)
def read_project_objective_events(
    project_id: int,
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_project_permission("project:read")),
):
    """認可した案件の業務履歴をページ取得する。"""
    response.headers["Cache-Control"] = "no-store"
    return service.events(db, user, project_id, page, page_size)
