"""既存の業務認可と正本による参照日程。"""

from datetime import date, timedelta

from sqlalchemy import select

from app.core import error_messages
from app.core.config import settings
from app.core.exceptions import BadRequestError, ForbiddenError
from app.models.project import ProjectMember
from app.repositories.requirement_schedule import RequirementScheduleRepository
from app.repositories.requirement_trace import RequirementTraceRepository
from app.schemas.requirement_schedule import (
    RequirementSchedule,
    ScheduleGroup,
    ScheduleSummary,
)
from app.services.project_objective import ProjectObjectiveService


class RequirementScheduleService:
    """所属/権限を毎回確認し、表示と集計の範囲を明示する。"""

    def read(
        self,
        db,
        user,
        project_id,
        page,
        common_page,
        document_id,
        q,
        status,
        window_start,
        window_end,
        metadata_only=False,
    ):
        """既存のガントや業務データには書き込まない。"""
        access = ProjectObjectiveService()
        access.access(db, user, project_id)
        if (
            db.scalar(
                select(ProjectMember.id).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id == user.id,
                )
            )
            is None
        ):
            raise ForbiddenError()
        if not settings.project_overview_schedule_enabled:
            return RequirementSchedule(enabled=False)
        if not all(
            access.can(db, user, project_id, code)
            for code in ("task:read", "requirement:read")
        ):
            raise ForbiddenError()
        today = date.today()
        start = window_start or (today - timedelta(days=today.weekday()))
        end = window_end or start + timedelta(days=min(55, (date.max - start).days))
        if end < start or (end - start).days >= 84:
            raise BadRequestError(error_messages.PROJECT_SCHEDULE_RANGE_INVALID)
        result = RequirementSchedule(
            enabled=True, basis_date=today, window_start=start, window_end=end
        )
        if metadata_only:
            return result
        repo = RequirementScheduleRepository()
        requirements = repo.requirements(project_id, document_id, q, status)
        rows, total = RequirementTraceRepository().rows(
            db, project_id, page, 20, document_id, q, status
        )
        filtered = document_id is not None or bool(q) or bool(status)
        milestones = repo.milestones(db, project_id, start, end)
        common, common_total = (
            ([], None) if filtered else repo.common(db, project_id, common_page)
        )
        return RequirementSchedule(
            enabled=True,
            basis_date=today,
            window_start=start,
            window_end=end,
            page=page,
            total=total,
            groups=[
                ScheduleGroup.model_validate(row)
                for row in repo.groups(db, project_id, rows)
            ],
            summary=ScheduleSummary.model_validate(
                repo.summary(db, project_id, requirements, filtered, today)
            ),
            common_state="outside_requirement_filter" if filtered else "ready",
            common_page=common_page,
            common_total=common_total,
            common_tasks=common,
            milestones=milestones[:50],
            milestones_has_more=len(milestones) > 50,
        )
