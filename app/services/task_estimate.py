"""Keep the published task estimate and its calculation inputs in one transaction."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import BadRequestError, NotFoundError, VersionConflictError
from app.db.demo_scope import lock_active_demo
from app.models.task import Task
from app.models.task_estimate import TaskEstimateBaseline, TaskEstimateRevision
from app.repositories.task_estimate import (
    build_baseline,
    estimate_history,
    latest_baseline,
    latest_estimate,
    legacy_estimate,
)
from app.schemas.task import TaskUpdate
from app.schemas.task_estimate import (
    TaskEstimateBaselineConfirm,
    TaskEstimateBaselineRead,
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
        planned_minutes=task.planned_minutes,
        remaining_minutes=task.remaining_minutes,
        current=current,
        history=[TaskEstimateRevisionRead.model_validate(row) for row in items],
        history_total=total,
        page=page,
    )


def _lock_estimate_task(db: Session, task_id: int) -> Task:
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
    return task


def save_task_estimate(
    db: Session, task_id: int, data: TaskEstimateUpdate, actor_id: int
) -> TaskEstimateRead:
    task = _lock_estimate_task(db, task_id)
    revise_locked_estimate(db, task, data, actor_id)
    db.commit()
    return read_task_estimate(db, task)


def revise_locked_estimate(
    db: Session, task: Task, data: TaskEstimateUpdate, actor_id: int
) -> None:
    """ロック済みの行に改訂・監査を追加する。コミットは保存単位ごとに呼び出し元が行う。"""
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
            quantity=data.quantity,
            unit_hours=data.unit_hours,
            reference_work=data.reference_work,
            reference_minutes=data.reference_minutes,
            factor=data.factor,
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


def confirm_task_baseline(
    db: Session, task_id: int, data: TaskEstimateBaselineConfirm, actor_id: int
) -> TaskEstimateBaselineRead:
    task = _lock_estimate_task(db, task_id)
    estimate = latest_estimate(db, task.id)
    estimate_version = estimate.task_version if estimate else task.version
    current = latest_baseline(db, task.id)
    if (
        data.estimate_version == estimate_version
        and current
        and current.estimate.task_version == estimate_version
    ):
        return current
    if data.estimate_version != estimate_version or data.expected_baseline_id != (
        current.id if current else None
    ):
        raise VersionConflictError(
            current={
                "estimate_version": estimate_version,
                "baseline": current.model_dump(mode="json") if current else None,
            }
        )
    if task.estimated_minutes is None:
        raise BadRequestError(error_messages.TASK_ESTIMATE_BASELINE_MISSING)
    if estimate is None:
        estimate = TaskEstimateRevision(
            task_id=task.id,
            task_version=task.version,
            method="simple",
            minutes=task.estimated_minutes,
        )
        db.add(estimate)
        db.flush()
    baseline = TaskEstimateBaseline(
        estimate_revision_id=estimate.id,
        confirmed_by=actor_id,
        reason=(data.reason or "").strip() or None,
    )
    db.add(baseline)
    db.flush()
    result = build_baseline(baseline, estimate)
    AuditLogService().record(
        db,
        event_type="task.estimate_baseline_confirmed",
        actor_user_id=actor_id,
        project_id=task.project_id,
        resource_type="task",
        resource_id=task.id,
        metadata={"baseline_id": baseline.id, "estimate_version": estimate_version},
        commit=False,
    )
    db.commit()
    return result
