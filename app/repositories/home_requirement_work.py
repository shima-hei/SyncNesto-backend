"""本人の未処理のレビュー・承認・修正指摘をまとめて取得する。"""

from collections.abc import Sequence
from typing import NamedTuple

from sqlalchemy import (
    Date,
    Uuid,
    and_,
    cast,
    func,
    literal,
    null,
    or_,
    select,
    union_all,
)
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.models.artifact_release import ArtifactRelease, ArtifactReleaseNode
from app.models.project import Project, ProjectMember
from app.models.requirement_workflow import RequirementReviewThread as Thread
from app.models.requirement_workflow import (
    RequirementWorkflowParticipant as Participant,
)
from app.models.requirement_workflow import RequirementWorkflowRequest as Request


class ProjectAccess(NamedTuple):
    source_kinds: tuple[str, ...]
    can_approve: bool
    can_manage_threads: bool


class HomeRequirementWorkRepository:
    """所属・操作・固定内容の閲覧条件で未処理項目を絞り込む。"""

    def candidate_projects(
        self, db: Session, tenant_id: int, user_id: int
    ) -> list[int]:
        """本人への依頼や修正指摘がある有効な参加案件だけを返す。"""
        assignment = select(Participant.request_id).where(
            Participant.user_id == user_id, Participant.status == "pending"
        )
        corrections = select(Thread.request_id).where(self._correction_action(user_id))
        return list(
            db.scalars(
                select(Project.id)
                .join(ProjectMember, ProjectMember.project_id == Project.id)
                .where(
                    Project.tenant_id == tenant_id,
                    Project.deleted_at.is_(None),
                    ProjectMember.user_id == user_id,
                    ProjectMember.deleted_at.is_(None),
                    select(Request.id)
                    .where(
                        Request.project_id == Project.id,
                        or_(
                            and_(
                                Request.status == "requested",
                                Request.id.in_(assignment),
                            ),
                            and_(
                                Request.status != "cancelled",
                                Request.id.in_(corrections),
                            ),
                        ),
                    )
                    .exists(),
                )
                .distinct()
            )
        )

    @staticmethod
    def _correction_action(user_id: int):
        return and_(
            Thread.kind == "correction",
            or_(
                and_(
                    Thread.assignee_id == user_id,
                    Thread.status.in_(("open", "in_progress")),
                ),
                and_(
                    Thread.author_id == user_id,
                    Thread.status == "awaiting_confirmation",
                ),
            ),
        )

    def work(
        self,
        db: Session,
        user_id: int,
        projects: dict[ProjectAccess, list[int]],
        page: int,
        page_size: int,
    ) -> tuple[int, Sequence[RowMapping]]:
        """対象の期日・作成順で件数とページを返す。"""
        if not projects:
            return 0, []
        assigned = (
            select(Participant.request_id)
            .where(Participant.request_id == Request.id, Participant.user_id == user_id)
            .exists()
        )
        readable = {
            access: and_(
                Request.project_id.in_(project_ids),
                ArtifactRelease.source_kinds.contained_by(list(access.source_kinds)),
            )
            for access, project_ids in projects.items()
        }
        request_rows = (
            select(
                Request.id.label("id"),
                Request.id.label("request_id"),
                Request.project_id,
                Request.display_number,
                func.coalesce(
                    ArtifactReleaseNode.payload["title"].astext, ArtifactRelease.title
                ).label("title"),
                Request.kind,
                Participant.status,
                Request.due_on,
                cast(null(), Uuid).label("node_id"),
                Request.created_at,
            )
            .select_from(Request)
            .join(Participant, Participant.request_id == Request.id)
            .join(
                ArtifactRelease,
                (ArtifactRelease.id == Request.release_id)
                & (ArtifactRelease.project_id == Request.project_id),
            )
            .join(
                ArtifactReleaseNode,
                (ArtifactReleaseNode.id == Request.target_node_id)
                & (ArtifactReleaseNode.release_id == Request.release_id),
            )
            .where(
                ArtifactRelease.sealed.is_(True),
                Request.status == "requested",
                Participant.user_id == user_id,
                Participant.status == "pending",
                or_(
                    *(
                        scope
                        if access.can_approve
                        else and_(scope, Request.kind == "review")
                        for access, scope in readable.items()
                    )
                ),
            )
        )
        corrections = (
            select(
                Thread.id.label("id"),
                Request.id.label("request_id"),
                Request.project_id,
                Request.display_number,
                func.left(Thread.body, 160).label("title"),
                literal("correction").label("kind"),
                Thread.status,
                cast(null(), Date).label("due_on"),
                Thread.node_id,
                Thread.created_at,
            )
            .select_from(Request)
            .join(Thread, Thread.request_id == Request.id)
            .join(
                ArtifactRelease,
                (ArtifactRelease.id == Request.release_id)
                & (ArtifactRelease.project_id == Request.project_id),
            )
            .where(
                ArtifactRelease.sealed.is_(True),
                Request.kind == "review",
                Request.status != "cancelled",
                self._correction_action(user_id),
                or_(
                    *(
                        scope
                        if access.can_manage_threads
                        else and_(scope, or_(Request.requester_id == user_id, assigned))
                        for access, scope in readable.items()
                    )
                ),
            )
        )
        work = union_all(request_rows, corrections).cte("requirement_work")
        total = db.scalar(select(func.count()).select_from(work)) or 0
        page_rows = (
            select(work, Project.name.label("project_name"))
            .join(Project, Project.id == work.c.project_id)
            .order_by(
                work.c.due_on.asc().nulls_last(),
                work.c.created_at.desc(),
                work.c.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return total, db.execute(page_rows).mappings().all()
