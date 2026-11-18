"""Project defaults and atomic task estimate worksheet saves."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError, VersionConflictError
from app.db.demo_scope import lock_active_demo
from app.models.project import Project
from app.models.task import Task
from app.repositories.task_estimate import worksheet_rows
from app.schemas.task_estimate import EstimateWorksheetSave, ProjectEstimateStandard
from app.services.audit_log import AuditLogService
from app.services.task_estimate import revise_locked_estimate


def project_standard(
    db: Session,
    project_id: int,
    update: ProjectEstimateStandard | None = None,
    actor_id: int | None = None,
) -> ProjectEstimateStandard:
    query = select(Project).where(
        Project.id == project_id, Project.deleted_at.is_(None)
    )
    if update is not None:
        if db.info.get("demo_id") is not None:
            lock_active_demo(db)
        query = query.with_for_update().execution_options(populate_existing=True)
    project = db.scalar(query)
    if project is None:
        raise NotFoundError()
    current = ProjectEstimateStandard.model_validate(
        {"method": project.estimate_method, "version": project.estimate_method_version}
    )
    if update is None:
        return current
    if update.version != current.version:
        raise VersionConflictError(current=current.model_dump())
    if update.method == current.method:
        return current
    project.estimate_method = update.method
    project.estimate_method_version += 1
    result = ProjectEstimateStandard(
        method=update.method, version=project.estimate_method_version
    )
    AuditLogService().record(
        db,
        event_type="project.estimate_standard_changed",
        actor_user_id=actor_id,
        project_id=project_id,
        resource_type="project",
        resource_id=project_id,
        metadata={
            "previous": current.method,
            "method": result.method,
            "version": result.version,
        },
        commit=False,
    )
    db.commit()
    return result


def save_worksheet(
    db: Session, project_id: int, batch: EstimateWorksheetSave, actor_id: int
):
    changes = {row.task_id: row for row in batch.rows}
    if db.info.get("demo_id") is not None:
        lock_active_demo(db)
    tasks = list(
        db.scalars(
            select(Task)
            .where(
                Task.project_id == project_id,
                Task.deleted_at.is_(None),
                Task.id.in_(changes.keys()),
            )
            .order_by(Task.id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
    )
    if len(tasks) != len(changes):
        raise NotFoundError()
    conflicts = [task.id for task in tasks if task.version != changes[task.id].version]
    if conflicts:
        raise VersionConflictError(current={"task_ids": conflicts})
    if any(task.estimated_basis != "own" for task in tasks):
        raise BadRequestError("自身の見積を集計するタスクを選択してください。")
    for task in tasks:
        revise_locked_estimate(db, task, changes[task.id], actor_id)
    db.flush()
    result = worksheet_rows(db, project_id, list(changes))
    db.commit()
    return result
