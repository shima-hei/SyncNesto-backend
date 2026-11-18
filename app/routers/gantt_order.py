"""ガントの表示順API。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.gantt_order import GanttOrderMove
from app.services.gantt_order import reorder_gantt_task
from app.services.task_phase import TaskPhaseService

router = APIRouter(tags=["gantt-order"])
phase_service = TaskPhaseService()


@router.patch("/projects/{project_id}/gantt/task-order", status_code=204)
def move_gantt_task(
    project_id: int,
    data: GanttOrderMove,
    user: User = Depends(require_project_permission("task:update")),
    db: Session = Depends(get_db),
) -> None:
    """同じ工程・親の配下でタスクのガント表示順を変更する。"""
    reorder_gantt_task(db, project_id, data, user.id)


@router.patch("/projects/{project_id}/gantt/phase-order", status_code=204)
def move_gantt_phase(
    project_id: int,
    data: GanttOrderMove,
    user: User = Depends(require_project_permission("project:update")),
    db: Session = Depends(get_db),
) -> None:
    """工程の表示順をまとめて変更する。"""
    phase_service.reorder(db, project_id, data, user.id)
