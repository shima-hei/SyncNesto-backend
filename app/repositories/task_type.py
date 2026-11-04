"""案件境界で種別を取得するRepository。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.task import Task
from app.models.task_type import ProjectTaskType


class TaskTypeRepository:
    """非表示行も参照・重複判定のため保持する。"""

    def list(self, db: Session, project_ids: list[int]) -> list[ProjectTaskType]:
        """複数Taskの表示名を一括取得する。"""
        if not project_ids:
            return []
        return list(
            db.scalars(
                select(ProjectTaskType)
                .where(ProjectTaskType.project_id.in_(project_ids))
                .order_by(ProjectTaskType.id)
            )
        )

    def get(self, db: Session, project_id: int, key: str, *, lock=False):
        """案件と安定キーで取得し、必要な場合は更新を直列化する。"""
        stmt = select(ProjectTaskType).where(
            ProjectTaskType.project_id == project_id, ProjectTaskType.key == key
        )
        return db.scalar(stmt.with_for_update() if lock else stmt)

    def legacy_keys(self, db: Session, project_id: int):
        """既存APIで保存された自由文字列も選択・表示を維持する。"""
        return db.scalars(
            select(Task.task_type)
            .where(Task.project_id == project_id, Task.deleted_at.is_(None))
            .distinct()
            .order_by(Task.task_type)
        )
