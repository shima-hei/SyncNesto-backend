"""タスク管理Repositoryを定義するモジュール。"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.task import (
    Milestone,
)
from app.schemas.task import (
    MilestoneCreate,
    MilestoneUpdate,
)


class MilestoneRepository:
    """Milestoneテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        project_id: int,
        milestone_in: MilestoneCreate,
        actor_id: int | None,
    ) -> Milestone:
        """マイルストーンを作成する。"""
        milestone = Milestone(
            project_id=project_id,
            title=milestone_in.title,
            description=milestone_in.description,
            target_date=milestone_in.target_date,
            status=milestone_in.status,
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(milestone)
        db.commit()
        db.refresh(milestone)
        return milestone

    def get_by_id(self, db: Session, milestone_id: int) -> Milestone | None:
        """idに一致する未削除マイルストーンを取得する。"""
        return (
            db.query(Milestone)
            .filter(Milestone.id == milestone_id, Milestone.deleted_at.is_(None))
            .first()
        )

    def list_by_project(self, db: Session, project_id: int) -> list[Milestone]:
        """プロジェクト内マイルストーン一覧を取得する。"""
        return (
            db.query(Milestone)
            .filter(Milestone.project_id == project_id, Milestone.deleted_at.is_(None))
            .order_by(Milestone.target_date, Milestone.id)
            .all()
        )

    def update(
        self,
        db: Session,
        *,
        milestone: Milestone,
        milestone_in: MilestoneUpdate,
        actor_id: int | None,
    ) -> Milestone:
        """マイルストーンを更新する。"""
        for field in ["title", "description", "target_date", "status"]:
            if field in milestone_in.model_fields_set:
                setattr(milestone, field, getattr(milestone_in, field))
        milestone.updated_by = actor_id
        milestone.version += 1
        db.commit()
        db.refresh(milestone)
        return milestone

    def soft_delete(
        self,
        db: Session,
        *,
        milestone: Milestone,
        actor_id: int | None,
    ) -> Milestone:
        """マイルストーンを論理削除する。"""
        milestone.deleted_at = datetime.now(UTC)
        milestone.updated_by = actor_id
        milestone.version += 1
        db.commit()
        db.refresh(milestone)
        return milestone
