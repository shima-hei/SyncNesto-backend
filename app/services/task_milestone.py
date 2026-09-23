"""タスクマイルストーンのビジネスロジックを提供するService。"""

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import NotFoundError
from app.models.task import Milestone
from app.repositories.project import ProjectRepository
from app.repositories.task_change_log import TaskChangeLogRepository
from app.repositories.task_milestone import MilestoneRepository
from app.schemas.task import MilestoneCreate, MilestoneUpdate
from app.services.conflict import build_conflict_current, raise_if_version_conflict

MILESTONE_CONFLICT_CURRENT_FIELDS = (
    "title",
    "description",
    "target_date",
    "status",
    "id",
    "project_id",
    "version",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
)


class TaskMilestoneAction:
    """マイルストーン変更履歴の操作種別定数。"""

    CREATED = "milestone.created"
    UPDATED = "milestone.updated"
    DELETED = "milestone.deleted"


class TaskMilestoneService:
    """タスクマイルストーンのビジネスロジックを提供する。"""

    def __init__(
        self,
        *,
        milestone_repository: MilestoneRepository | None = None,
        project_repository: ProjectRepository | None = None,
        change_log_repository: TaskChangeLogRepository | None = None,
    ) -> None:
        """TaskMilestoneServiceを初期化する。"""
        self.milestone_repository = milestone_repository or MilestoneRepository()
        self.project_repository = project_repository or ProjectRepository()
        self.change_log_repository = change_log_repository or TaskChangeLogRepository()

    def create_milestone(
        self,
        db: Session,
        *,
        project_id: int,
        milestone_in: MilestoneCreate,
        actor_id: int | None,
    ) -> Milestone:
        """マイルストーンを作成する。"""
        self._ensure_project_exists(db, project_id)
        milestone = self.milestone_repository.create(
            db,
            project_id=project_id,
            milestone_in=milestone_in,
            actor_id=actor_id,
        )
        self._record_milestone_change(
            db,
            milestone=milestone,
            action=TaskMilestoneAction.CREATED,
            changed_by=actor_id,
        )
        return milestone

    def list_milestones(self, db: Session, project_id: int) -> list[Milestone]:
        """プロジェクト内マイルストーン一覧を取得する。"""
        self._ensure_project_exists(db, project_id)
        return self.milestone_repository.list_by_project(db, project_id)

    def get_milestone(self, db: Session, milestone_id: int) -> Milestone:
        """マイルストーンを取得する。"""
        milestone = self.milestone_repository.get_by_id(db, milestone_id)
        if milestone is None:
            raise NotFoundError(error_messages.MILESTONE_NOT_FOUND)
        return milestone

    def update_milestone(
        self,
        db: Session,
        *,
        milestone_id: int,
        milestone_in: MilestoneUpdate,
        actor_id: int | None,
    ) -> Milestone:
        """マイルストーンを更新する。"""
        milestone = self.get_milestone(db, milestone_id)
        self._raise_if_milestone_version_conflict(
            milestone,
            milestone_in.version,
        )
        milestone = self.milestone_repository.update(
            db,
            milestone=milestone,
            milestone_in=milestone_in,
            actor_id=actor_id,
        )
        self._record_milestone_change(
            db,
            milestone=milestone,
            action=TaskMilestoneAction.UPDATED,
            changed_by=actor_id,
        )
        return milestone

    def delete_milestone(
        self,
        db: Session,
        *,
        milestone_id: int,
        actor_id: int | None,
    ) -> None:
        """マイルストーンを論理削除する。"""
        milestone = self.get_milestone(db, milestone_id)
        self.milestone_repository.soft_delete(
            db,
            milestone=milestone,
            actor_id=actor_id,
        )
        self._record_milestone_change(
            db,
            milestone=milestone,
            action=TaskMilestoneAction.DELETED,
            changed_by=actor_id,
        )

    def _ensure_project_exists(self, db: Session, project_id: int) -> None:
        """プロジェクトが存在することを確認する。"""
        if self.project_repository.get_by_id(db, project_id) is None:
            raise NotFoundError(error_messages.PROJECT_NOT_FOUND)

    def _raise_if_milestone_version_conflict(
        self,
        milestone: Milestone,
        requested_version: int,
    ) -> None:
        """マイルストーン更新時の楽観的排他制御を検証する。"""
        raise_if_version_conflict(
            current_version=milestone.version,
            requested_version=requested_version,
            current=build_conflict_current(
                milestone,
                MILESTONE_CONFLICT_CURRENT_FIELDS,
            ),
        )

    def _record_milestone_change(
        self,
        db: Session,
        *,
        milestone: Milestone,
        action: str,
        changed_by: int | None,
    ) -> None:
        """マイルストーン変更履歴を記録する。"""
        self.change_log_repository.create(
            db,
            project_id=milestone.project_id,
            target_type="milestone",
            target_id=milestone.id,
            action=action,
            changed_by=changed_by,
        )
