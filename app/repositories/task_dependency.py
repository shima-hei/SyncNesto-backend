"""タスク管理Repositoryを定義するモジュール。"""


from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.task import (
    Task,
    TaskDependency,
)
from app.schemas.task import (
    TaskDependencyCreate,
    TaskDependencyUpdate,
)


class TaskDependencyRepository:
    """TaskDependencyテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        dependency_in: TaskDependencyCreate,
        actor_id: int | None,
    ) -> TaskDependency:
        """タスク依存関係を作成する。"""
        dependency = TaskDependency(
            predecessor_task_id=dependency_in.predecessor_task_id,
            successor_task_id=dependency_in.successor_task_id,
            dependency_type=dependency_in.dependency_type,
            lag_days=dependency_in.lag_days,
            created_by=actor_id,
        )
        db.add(dependency)
        db.commit()
        db.refresh(dependency)
        return dependency

    def get_by_id(self, db: Session, dependency_id: int) -> TaskDependency | None:
        """idに一致するタスク依存関係を取得する。"""
        return (
            db.query(TaskDependency)
            .filter(TaskDependency.id == dependency_id)
            .first()
        )

    def list_by_task(self, db: Session, task_id: int) -> list[TaskDependency]:
        """対象タスクに関係する依存関係一覧を取得する。"""
        return (
            db.query(TaskDependency)
            .filter(
                or_(
                    TaskDependency.predecessor_task_id == task_id,
                    TaskDependency.successor_task_id == task_id,
                )
            )
            .order_by(TaskDependency.id)
            .all()
        )

    def list_by_project(self, db: Session, project_id: int) -> list[TaskDependency]:
        """プロジェクト内タスクの依存関係一覧を取得する。"""
        return (
            db.query(TaskDependency)
            .join(Task, TaskDependency.successor_task_id == Task.id)
            .filter(Task.project_id == project_id, Task.deleted_at.is_(None))
            .order_by(TaskDependency.id)
            .all()
        )

    def list_successors(self, db: Session, task_id: int) -> list[TaskDependency]:
        """指定タスクを先行タスクとする依存関係一覧を取得する。"""
        return (
            db.query(TaskDependency)
            .filter(TaskDependency.predecessor_task_id == task_id)
            .all()
        )

    def update(
        self,
        db: Session,
        *,
        dependency: TaskDependency,
        dependency_in: TaskDependencyUpdate,
    ) -> TaskDependency:
        """タスク依存関係を更新する。"""
        if dependency_in.dependency_type is not None:
            dependency.dependency_type = dependency_in.dependency_type
        if dependency_in.lag_days is not None:
            dependency.lag_days = dependency_in.lag_days
        dependency.version += 1
        db.commit()
        db.refresh(dependency)
        return dependency

    def delete(self, db: Session, dependency: TaskDependency) -> None:
        """タスク依存関係を削除する。"""
        db.delete(dependency)
        db.commit()
