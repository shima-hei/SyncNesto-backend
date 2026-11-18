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
from app.repositories.task_phase import (
    EFFORT_METRICS,
    TaskPhaseRepository,
    effort_totals,
)
from app.schemas.gantt_order import GanttOrderMove
from app.schemas.task_phase import (
    AssigneeEffortGroup,
    EffortFilters,
    EffortSummary,
    PhaseAssignment,
    PhaseCreate,
    PhaseEffortGroup,
    PhaseEventList,
    PhaseEventRead,
    PhaseList,
    PhaseListItem,
    PhaseRead,
    PhaseUpdate,
    StatusEffortGroup,
)
from app.services.audit_log import AuditLogService
from app.services.conflict import (
    raise_duplicate_after_rollback,
    raise_if_version_conflict,
)
from app.services.gantt_order import lock_gantt_project, move_gantt_item


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
        ).all()
        summaries = (
            self.repository.planning_summaries(db, project_id, [row.id for row in rows])
            if rows
            else {}
        )
        return PhaseList(
            items=[
                PhaseListItem.model_validate(row).model_copy(
                    update=summaries.get(row.id, {})
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
            for field in (
                "name",
                "sort_order",
                "color",
                "description",
                "reserve_minutes",
            ):
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
        self._record_change(db, result, old, actor_id)
        db.commit()
        result.task_count = self.repository.counts(db, project_id).get(phase.id, 0)
        return result

    def reorder(
        self, db: Session, project_id: int, move: GanttOrderMove, actor_id: int
    ) -> None:
        lock_gantt_project(db, project_id)
        phases = self.repository.list_for_reorder(db, project_id)
        by_id = {phase.id: phase for phase in phases}
        ids = list(by_id)
        reordered = move_gantt_item(ids, move)
        if reordered == ids:
            return

        for index, phase_id in enumerate(reordered):
            phase = by_id[phase_id]
            if phase.sort_order == index:
                continue
            old = PhaseRead.model_validate(phase).model_dump(
                mode="json", exclude={"task_count"}
            )
            phase.sort_order = index
            phase.updated_by = actor_id
            phase.version += 1
            db.flush([phase])
            self._record_change(db, PhaseRead.model_validate(phase), old, actor_id)
        db.commit()

    @staticmethod
    def _record_change(db: Session, result: PhaseRead, old: dict | None, actor_id: int):
        db.add(
            ProjectPhaseEvent(
                project_id=result.project_id,
                phase_id=result.id,
                actor_id=actor_id,
                old_value=old,
                new_value=result.model_dump(mode="json", exclude={"task_count"}),
            )
        )
        AuditLogService().record(
            db,
            event_type="task_phase.updated" if old else "task_phase.created",
            actor_user_id=actor_id,
            project_id=result.project_id,
            resource_type="task_phase",
            resource_id=result.id,
            metadata={"old": old, "new": result.model_dump(mode="json")},
            commit=False,
        )

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
        keys = (
            "task_count",
            *(
                f"{metric}_{field}"
                for metric in EFFORT_METRICS
                for field in ("minutes", "missing", "unreviewed")
            ),
        )
        sums = dict.fromkeys(keys, 0)
        phase_sums = {}
        status_sums = {}
        assignee_sums = {}
        # 同一SQLの行から内訳を作り、同時更新でもProject小計とずらさない。
        for row in rows:
            phase_sum = phase_sums.setdefault(
                (row["phase_scope"], row["phase_id"]), dict.fromkeys(keys, 0)
            )
            status_sum = status_sums.setdefault(row["status"], dict.fromkeys(keys, 0))
            assignee_sum = assignee_sums.setdefault(
                row["assignee_id"], dict.fromkeys(keys, 0)
            )
            for key in keys:
                value = int(row[key] or 0)
                sums[key] += value
                phase_sum[key] += value
                status_sum[key] += value
                assignee_sum[key] += value
        groups = [
            PhaseEffortGroup(
                kind=kind,
                phase=PhaseRead.model_validate(phases[phase_id]).model_copy(
                    update={"task_count": phase_counts.get(phase_id, 0)}
                )
                if phase_id in phases
                else None,
                totals=effort_totals(values),
            )
            for (kind, phase_id), values in phase_sums.items()
        ]
        return EffortSummary(
            project_id=project_id,
            filters=filters,
            totals=effort_totals(sums),
            groups=groups,
            status_groups=[
                StatusEffortGroup(status=status, totals=effort_totals(values))
                for status, values in sorted(status_sums.items())
            ],
            assignee_groups=[
                AssigneeEffortGroup(
                    assignee_id=assignee_id, totals=effort_totals(values)
                )
                for assignee_id, values in sorted(
                    assignee_sums.items(),
                    key=lambda item: (
                        -item[1]["remaining_minutes"],
                        item[0] is None,
                        item[0] or 0,
                    ),
                )
            ],
        )
