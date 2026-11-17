"""Apply versioned task assignments and optional phase archiving together."""

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.db.demo_scope import lock_active_demo
from app.models.task import Task
from app.schemas.task import TaskUpdate
from app.schemas.task_phase import (
    PhaseArchiveMove,
    PhaseArchivePreview,
    PhaseAssignment,
    PhaseTransfer,
    PhaseTransferResult,
    PhaseUpdate,
)
from app.services.task import TaskService
from app.services.task_phase import TaskPhaseService


class PhaseTransferService:
    def __init__(self):
        self.phases = TaskPhaseService()
        self.tasks = TaskService()

    @staticmethod
    def _members(db: Session, project_id: int, phase_id: int):
        return db.execute(
            select(Task.id, Task.version)
            .where(
                Task.project_id == project_id,
                Task.phase_id == phase_id,
                Task.deleted_at.is_(None),
            )
            .order_by(Task.id)
        ).all()

    @staticmethod
    def _snapshot(phase_id: int, phase_version: int, members) -> str:
        rows = ",".join(f"{task_id}:{version}" for task_id, version in members)
        return hashlib.sha256(f"{phase_id}:{phase_version}/{rows}".encode()).hexdigest()

    def preview(
        self, db: Session, project_id: int, phase_id: int
    ) -> PhaseArchivePreview:
        phase = self.phases.read(db, project_id, phase_id)
        if phase.archived_at is not None:
            raise ConflictError(error_messages.TASK_PHASE_TRANSFER_STALE)
        members = self._members(db, project_id, phase_id)
        return PhaseArchivePreview(
            phase_id=phase_id,
            task_count=len(members),
            snapshot=self._snapshot(phase_id, phase.version, members),
        )

    @staticmethod
    def _lock_tasks(db: Session, project_id: int, ids: list[int]) -> list[Task]:
        if db.info.get("demo_id") is not None:
            lock_active_demo(db)
        if not ids:
            return []
        # Keep the task-before-phase order of single task edits. Child inserts
        # may still take key-share locks on the task IDs while a move is running.
        return list(
            db.scalars(
                select(Task)
                .where(
                    Task.project_id == project_id,
                    Task.id.in_(ids),
                    Task.deleted_at.is_(None),
                )
                .order_by(Task.id)
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
        )

    def _move(
        self,
        db: Session,
        rows: list[Task],
        versions: dict[int, int],
        destination: PhaseAssignment,
        actor_id: int,
    ) -> int:
        moved = 0
        for task in rows:
            if (task.phase_scope, task.phase_id) == (
                destination.kind,
                destination.phase_id,
            ):
                continue
            self.tasks.move_locked_task_to_phase(
                db,
                task,
                TaskUpdate(version=versions[task.id], phase_assignment=destination),
                actor_id,
            )
            moved += 1
        return moved

    def transfer(
        self, db: Session, project_id: int, data: PhaseTransfer, actor_id: int
    ) -> PhaseTransferResult:
        versions = {task.id: task.version for task in data.tasks}
        if len(versions) != len(data.tasks):
            raise BadRequestError(error_messages.TASK_PHASE_TRANSFER_STALE)
        rows = self._lock_tasks(db, project_id, list(versions))
        if len(rows) != len(versions):
            raise NotFoundError(error_messages.TASK_NOT_FOUND)
        if any(task.version != versions[task.id] for task in rows):
            raise ConflictError(error_messages.TASK_PHASE_TRANSFER_STALE)
        self.phases.validate_assignment(db, project_id, data.destination)
        moved = self._move(db, rows, versions, data.destination, actor_id)
        db.commit()
        return PhaseTransferResult(moved_count=moved)

    def archive(
        self,
        db: Session,
        project_id: int,
        phase_id: int,
        data: PhaseArchiveMove,
        actor_id: int,
    ) -> PhaseTransferResult:
        destination = data.destination
        if phase_id == destination.phase_id:
            raise BadRequestError(error_messages.TASK_PHASE_TRANSFER_SAME)
        members = self._members(db, project_id, phase_id)
        rows = self._lock_tasks(db, project_id, [task_id for task_id, _ in members])
        phases = {
            item_id: self.phases.read(db, project_id, item_id, lock=True)
            for item_id in sorted({phase_id, destination.phase_id} - {None})
        }
        phase = phases[phase_id]
        current = self._members(db, project_id, phase_id)
        if (
            phase.archived_at is not None
            or current != members
            or self._snapshot(phase_id, phase.version, current) != data.snapshot
        ):
            raise ConflictError(error_messages.TASK_PHASE_TRANSFER_STALE)
        self.phases.validate_assignment(db, project_id, destination)
        moved = self._move(db, rows, dict(current), destination, actor_id)
        self.phases.save(
            db,
            project_id,
            PhaseUpdate(version=phase.version, archived=True),
            actor_id,
            phase_id,
        )
        return PhaseTransferResult(moved_count=moved)
