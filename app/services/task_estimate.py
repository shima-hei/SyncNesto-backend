"""Keep the published task estimate and its calculation inputs in one transaction."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.db.demo_scope import lock_active_demo
from app.models.task import Task
from app.models.task_estimate import TaskEstimateRevision
from app.repositories.task_estimate import (
    estimate_history,
    latest_estimate,
    legacy_estimate,
)
from app.schemas.task import TaskUpdate
from app.schemas.task_estimate import (
    TaskEstimateRead,
    TaskEstimateRevisionRead,
    TaskEstimateUpdate,
)
from app.services.audit_log import AuditLogService
from app.services.task import TaskService


def read_task_estimate(db: Session, task: Task, page: int = 1) -> TaskEstimateRead:
    items, total = estimate_history(db, task.id, page)
    newest = items[0] if page == 1 and items else latest_estimate(db, task.id)
    current = (
        TaskEstimateRevisionRead.model_validate(newest)
        if newest
        else legacy_estimate(task)
    )
    return TaskEstimateRead(
        version=task.version,
        current=current,
        history=[TaskEstimateRevisionRead.model_validate(row) for row in items],
        history_total=total,
        page=page,
    )


def save_task_estimate(
    db: Session, task_id: int, data: TaskEstimateUpdate, actor_id: int
) -> TaskEstimateRead:
    if db.info.get("demo_id") is not None:
        lock_active_demo(db)
    task = db.scalar(
        select(Task)
        .where(Task.id == task_id, Task.deleted_at.is_(None))
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if task is None:
        raise NotFoundError()
    previous = legacy_estimate(task)
    had_revision = latest_estimate(db, task.id) is not None
    minutes = data.calculated_minutes()
    TaskService().update_locked_task(
        db,
        task,
        TaskUpdate(
            version=data.version,
            estimated_minutes=minutes,
            change_reason=data.change_reason,
        ),
        actor_id,
        planning_only=True,
    )
    if not had_revision and previous.minutes is not None:
        db.add(
            TaskEstimateRevision(
                task_id=task.id,
                task_version=previous.task_version,
                method="simple",
                minutes=previous.minutes,
            )
        )
    db.add(
        TaskEstimateRevision(
            task_id=task.id,
            task_version=task.version,
            method=data.method,
            minutes=minutes,
            optimistic_minutes=data.optimistic_minutes,
            likely_minutes=data.likely_minutes,
            pessimistic_minutes=data.pessimistic_minutes,
            assumptions=data.assumptions,
            change_reason=data.change_reason,
            actor_id=actor_id,
        )
    )
    AuditLogService().record(
        db,
        event_type="task.estimate_revised",
        actor_user_id=actor_id,
        project_id=task.project_id,
        resource_type="task",
        resource_id=task.id,
        metadata={"method": data.method, "minutes": minutes},
        commit=False,
    )
    db.commit()
    return read_task_estimate(db, task)
