"""工程管理・所属検証・工数の業務契約。"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.requirement import Requirement, RequirementDocument
from app.models.task import Task
from app.models.task_phase import ProjectPhase, ProjectPhaseEvent
from app.repositories.task_phase import TaskPhaseRepository, effort_totals
from app.schemas.task_phase import (
    EffortFilters,
    EffortSummary,
    PhaseAssignment,
    PhaseCreate,
    PhaseEffortGroup,
    PhaseEventList,
    PhaseEventRead,
    PhaseList,
    PhaseRead,
    PhaseUpdate,
)
from app.services.audit_log import AuditLogService
from app.services.conflict import (
    raise_duplicate_after_rollback,
    raise_if_version_conflict,
)


class TaskPhaseService:
    """権限確認済みProject内で工程を管理する。"""

    def __init__(self):
        self.repository = TaskPhaseRepository()

    def events(self, db: Session, project_id: int, phase_id: int, page: int):
        """現在のProject境界で当時の名前を読み取る。"""
        self.read(db, project_id, phase_id)
        rows, total = self.repository.events(db, project_id, phase_id, page)
        return PhaseEventList(
            items=[PhaseEventRead.model_validate(row) for row in rows],
            total=total,
            page=page,
        )

    def read(self, db: Session, project_id: int, phase_id: int, *, lock=False):
        """アーカイブ済みでも同じIDを解決する。"""
        phase = self.repository.get(db, project_id, phase_id, lock=lock)
        if phase is None:
            raise NotFoundError(error_messages.TASK_PHASE_NOT_FOUND)
        return phase

    def validate_assignment(
        self,
        db: Session,
        project_id: int,
        assignment: PhaseAssignment | None,
        current_phase_id: int | None = None,
    ):
        """アーカイブとの同時更新を直列化する。"""
        if assignment is not None and assignment.phase_id is not None:
            phase = self.read(db, project_id, assignment.phase_id, lock=True)
            if phase.archived_at is not None and current_phase_id != phase.id:
                raise BadRequestError(error_messages.TASK_PHASE_ARCHIVED)

    def list(
        self,
        db: Session,
        project_id: int,
        page: int,
        page_size: int,
        include_archived: bool,
    ):
        """ページ数と全件数を返す。"""
        stmt = select(ProjectPhase).where(ProjectPhase.project_id == project_id)
        if not include_archived:
            stmt = stmt.where(ProjectPhase.archived_at.is_(None))
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = db.scalars(
            stmt.order_by(ProjectPhase.sort_order, ProjectPhase.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        counts = self.repository.counts(db, project_id)
        return PhaseList(
            items=[
                PhaseRead.model_validate(row).model_copy(
                    update={"task_count": counts.get(row.id, 0)}
                )
                for row in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    def save(
        self,
        db: Session,
        project_id: int,
        data: PhaseCreate | PhaseUpdate,
        actor_id: int,
        phase_id: int | None = None,
    ):
        """工程と当時の名前の履歴を同じtransactionで保存する。"""
        phase = (
            self.read(db, project_id, phase_id, lock=True)
            if phase_id is not None
            else ProjectPhase(
                project_id=project_id, name=data.name, created_by=actor_id
            )
        )
        old = (
            PhaseRead.model_validate(phase).model_dump(
                mode="json", exclude={"task_count"}
            )
            if phase_id is not None
            else None
        )
        if isinstance(data, PhaseUpdate):
            current = PhaseRead.model_validate(phase).model_copy(
                update={
                    "task_count": self.repository.counts(db, project_id).get(
                        phase.id, 0
                    )
                }
            )
            raise_if_version_conflict(
                current_version=phase.version,
                requested_version=data.version,
                current=current.model_dump(mode="json"),
            )
            for field in ("name", "sort_order"):
                if field in data.model_fields_set:
                    setattr(phase, field, getattr(data, field))
            if "archived" in data.model_fields_set:
                phase.archived_at = (
                    (phase.archived_at or datetime.now(UTC)) if data.archived else None
                )
            phase.version += 1
        else:
            phase.sort_order = data.sort_order
        phase.updated_by = actor_id
        db.add(phase)
        try:
            db.flush()
        except IntegrityError as exc:
            raise_duplicate_after_rollback(
                db, error_messages.TASK_PHASE_NAME_EXISTS, exc
            )
        result = PhaseRead.model_validate(phase)
        db.add(
            ProjectPhaseEvent(
                project_id=project_id,
                phase_id=phase.id,
                actor_id=actor_id,
                old_value=old,
                new_value=result.model_dump(mode="json", exclude={"task_count"}),
            )
        )
        AuditLogService().record(
            db,
            event_type="task_phase.updated" if old else "task_phase.created",
            actor_user_id=actor_id,
            project_id=project_id,
            resource_type="task_phase",
            resource_id=phase.id,
            metadata={"old": old, "new": result.model_dump(mode="json")},
            commit=False,
        )
        db.commit()
        result.task_count = self.repository.counts(db, project_id).get(phase.id, 0)
        return result

    def summary(self, db: Session, project_id: int, filters: EffortFilters):
        """工程・共通・未分類の和をProject全体と一致させる。"""
        if filters.root_only and filters.parent_task_id is not None:
            raise BadRequestError(error_messages.TASK_PARENT_FILTER_CONFLICT)
        if (
            filters.requirement_id is not None
            and db.scalar(
                select(Requirement.id)
                .join(RequirementDocument)
                .where(
                    Requirement.id == filters.requirement_id,
                    Requirement.deleted_at.is_(None),
                    RequirementDocument.project_id == project_id,
                    RequirementDocument.deleted_at.is_(None),
                )
            )
            is None
        ):
            raise NotFoundError(error_messages.REQUIREMENT_NOT_FOUND)
        if (
            filters.parent_task_id is not None
            and db.scalar(
                select(Task.id).where(
                    Task.id == filters.parent_task_id,
                    Task.project_id == project_id,
                    Task.deleted_at.is_(None),
                )
            )
            is None
        ):
            raise NotFoundError(error_messages.TASK_PARENT_NOT_FOUND)
        rows = self.repository.summary(db, project_id, filters)
        phase_counts = self.repository.counts(db, project_id)
        phases = {
            p.id: p
            for p in db.scalars(
                select(ProjectPhase).where(ProjectPhase.project_id == project_id)
            )
        }
        sums = {
            key: sum(int(row[key] or 0) for row in rows)
            for key in (
                "task_count",
                "estimated_minutes",
                "actual_minutes",
                "estimated_missing",
                "actual_missing",
                "estimated_unreviewed",
                "actual_unreviewed",
            )
        }
        groups = [
            PhaseEffortGroup(
                kind=row["phase_scope"],
                phase=PhaseRead.model_validate(phases[row["phase_id"]]).model_copy(
                    update={"task_count": phase_counts.get(row["phase_id"], 0)}
                )
                if row["phase_id"] in phases
                else None,
                totals=effort_totals(row),
            )
            for row in rows
        ]
        return EffortSummary(
            project_id=project_id,
            filters=filters,
            totals=effort_totals(sums),
            groups=groups,
        )
