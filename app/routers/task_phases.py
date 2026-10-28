"""既存Project権限内での工程と工数API。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.task_phase import (
    EffortFilters,
    EffortSummary,
    PhaseCreate,
    PhaseEventList,
    PhaseList,
    PhaseRead,
    PhaseUpdate,
)
from app.services.task_phase import TaskPhaseService

router = APIRouter(tags=["task-phases"])
service = TaskPhaseService()


@router.get("/projects/{project_id}/phases", response_model=PhaseList)
def list_phases(
    project_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    include_archived: bool = False,
    user: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
):
    """アーカイブ候補を明示してページ取得する。"""
    return service.list(db, project_id, page, page_size, include_archived)


@router.post("/projects/{project_id}/phases", response_model=PhaseRead, status_code=201)
def create_phase(
    project_id: int,
    data: PhaseCreate,
    user: User = Depends(require_project_permission("project:update")),
    db: Session = Depends(get_db),
):
    """工程を作成する。"""
    return service.save(db, project_id, data, user.id)


@router.patch("/projects/{project_id}/phases/{phase_id}", response_model=PhaseRead)
def update_phase(
    project_id: int,
    phase_id: int,
    data: PhaseUpdate,
    user: User = Depends(require_project_permission("project:update")),
    db: Session = Depends(get_db),
):
    """改名・並び順・アーカイブをversion付きで保存する。"""
    return service.save(db, project_id, data, user.id, phase_id)


@router.get(
    "/projects/{project_id}/phases/{phase_id}/events", response_model=PhaseEventList
)
def phase_events(
    project_id: int,
    phase_id: int,
    page: int = Query(1, ge=1),
    user: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
):
    """改名時点の名前を保持した履歴をページ取得する。"""
    return service.events(db, project_id, phase_id, page)


@router.get("/projects/{project_id}/tasks/effort-summary", response_model=EffortSummary)
def effort_summary(
    project_id: int,
    filters: EffortFilters = Depends(),
    user: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
):
    """ページや折り畳みと独立した一意Task集計。"""
    return service.summary(db, project_id, filters)
