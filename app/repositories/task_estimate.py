"""Read and append task estimate revisions under the caller's task scope."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.task import Task
from app.models.task_estimate import TaskEstimateBaseline, TaskEstimateRevision
from app.schemas.task_estimate import (
    EstimateWorksheetRow,
    TaskEstimateBaselineRead,
    TaskEstimateBaselines,
    TaskEstimateMethodRead,
    TaskEstimateRevisionRead,
)

ESTIMATE_HISTORY_PAGE_SIZE = 20


def _baselines(task_id: int):
    return (
        select(TaskEstimateBaseline, TaskEstimateRevision)
        .join(TaskEstimateRevision)
        .where(TaskEstimateRevision.task_id == task_id)
    )


def build_baseline(baseline: TaskEstimateBaseline, estimate: TaskEstimateRevision):
    return TaskEstimateBaselineRead(
        id=baseline.id,
        estimate=TaskEstimateRevisionRead.model_validate(estimate),
        confirmed_by=baseline.confirmed_by,
        confirmed_at=baseline.confirmed_at,
        reason=baseline.reason,
    )


def latest_baseline(db: Session, task_id: int) -> TaskEstimateBaselineRead | None:
    result = db.execute(
        _baselines(task_id).order_by(TaskEstimateBaseline.id.desc()).limit(1)
    ).first()
    return build_baseline(*result) if result else None


def read_baselines(db: Session, task_id: int, page: int) -> TaskEstimateBaselines:
    query = _baselines(task_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = [
        build_baseline(baseline, revision)
        for baseline, revision in db.execute(
            query.order_by(TaskEstimateBaseline.id.desc())
            .offset((page - 1) * ESTIMATE_HISTORY_PAGE_SIZE)
            .limit(ESTIMATE_HISTORY_PAGE_SIZE)
        )
    ]
    current = items[0] if page == 1 and items else latest_baseline(db, task_id)
    return TaskEstimateBaselines(current=current, items=items, total=total, page=page)


def estimate_history(db: Session, task_id: int, page: int):
    query = select(TaskEstimateRevision).where(TaskEstimateRevision.task_id == task_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = list(
        db.scalars(
            query.order_by(TaskEstimateRevision.task_version.desc())
            .offset((page - 1) * ESTIMATE_HISTORY_PAGE_SIZE)
            .limit(ESTIMATE_HISTORY_PAGE_SIZE)
        )
    )
    return items, total


def latest_estimate(db: Session, task_id: int) -> TaskEstimateRevision | None:
    return db.scalar(
        select(TaskEstimateRevision)
        .where(TaskEstimateRevision.task_id == task_id)
        .order_by(TaskEstimateRevision.task_version.desc())
        .limit(1)
    )


def legacy_estimate(task: Task) -> TaskEstimateRevisionRead:
    return TaskEstimateRevisionRead(
        task_version=task.version,
        method="simple" if task.estimated_minutes is not None else None,
        minutes=task.estimated_minutes,
    )


def append_legacy_estimate(
    db: Session, task: Task, actor_id: int | None, change_reason: str | None
) -> None:
    if latest_estimate(db, task.id) is None:
        return
    estimate = legacy_estimate(task)
    db.add(
        TaskEstimateRevision(
            task_id=task.id,
            task_version=task.version,
            minutes=estimate.minutes,
            method=estimate.method,
            actor_id=actor_id,
            change_reason=change_reason[:500] if change_reason else None,
        )
    )


def estimate_methods(
    db: Session, project_id: int, task_ids: list[int]
) -> list[TaskEstimateMethodRead]:
    latest_method = (
        select(TaskEstimateRevision.method)
        .where(TaskEstimateRevision.task_id == Task.id)
        .order_by(TaskEstimateRevision.task_version.desc())
        .limit(1)
        .scalar_subquery()
    )
    rows = db.execute(
        select(Task.id, Task.estimated_minutes, latest_method).where(
            Task.project_id == project_id,
            Task.id.in_(task_ids),
            Task.deleted_at.is_(None),
        )
    ).all()
    return [
        TaskEstimateMethodRead(
            task_id=task_id,
            method=method or ("simple" if minutes is not None else None),
        )
        for task_id, minutes, method in rows
    ]


def worksheet_rows(
    db: Session, project_id: int, task_ids: list[int]
) -> list[EstimateWorksheetRow]:
    latest_id = (
        select(TaskEstimateRevision.id)
        .where(TaskEstimateRevision.task_id == Task.id)
        .order_by(TaskEstimateRevision.task_version.desc())
        .limit(1)
        .correlate(Task)
        .scalar_subquery()
    )
    rows = db.execute(
        select(Task, TaskEstimateRevision)
        .outerjoin(TaskEstimateRevision, TaskEstimateRevision.id == latest_id)
        .where(
            Task.project_id == project_id,
            Task.id.in_(task_ids),
            Task.deleted_at.is_(None),
        )
        .order_by(Task.id)
    )
    return [
        EstimateWorksheetRow(
            task_id=task.id,
            version=task.version,
            current=TaskEstimateRevisionRead.model_validate(revision)
            if revision
            else legacy_estimate(task),
        )
        for task, revision in rows
    ]
