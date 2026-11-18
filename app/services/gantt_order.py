"""ガント内の相対位置を案件内の表示順として保存する。"""

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import BadRequestError, NotFoundError
from app.repositories.project import ProjectRepository
from app.repositories.task_item import TaskRepository
from app.schemas.gantt_order import GanttOrderMove
from app.services.audit_log import AuditLogService


def lock_gantt_project(db: Session, project_id: int) -> None:
    if ProjectRepository().get_by_id(db, project_id, lock=True) is None:
        raise NotFoundError()


def move_gantt_item(ids: list[int], move: GanttOrderMove) -> list[int]:
    if move.item_id == move.anchor_id or not {
        move.item_id,
        move.anchor_id,
    }.issubset(ids):
        raise BadRequestError(error_messages.GANTT_ORDER_INVALID_TARGET)
    reordered = [item_id for item_id in ids if item_id != move.item_id]
    index = reordered.index(move.anchor_id)
    reordered.insert(index + (move.placement == "after"), move.item_id)
    return reordered


def reorder_gantt_task(
    db: Session, project_id: int, move: GanttOrderMove, actor_id: int
) -> None:
    lock_gantt_project(db, project_id)
    tasks = TaskRepository()
    task = tasks.get_by_id(db, move.item_id)
    if task is None or task.project_id != project_id:
        raise NotFoundError()
    ids = tasks.gantt_siblings(db, task)
    reordered = move_gantt_item(ids, move)
    if reordered == ids:
        return
    tasks.save_gantt_order(db, reordered)
    AuditLogService().record(
        db,
        event_type="task.gantt_order_updated",
        actor_user_id=actor_id,
        project_id=project_id,
        resource_type="task",
        resource_id=task.id,
        metadata=move.model_dump(),
        commit=False,
    )
    db.commit()
