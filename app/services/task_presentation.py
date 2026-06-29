"""タスクレスポンスの表示補助データを収集するService。"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.task import Task, TaskDependency
from app.repositories.task_dependency import TaskDependencyRepository
from app.repositories.task_item import TaskRepository
from app.repositories.task_requirement import RequirementTaskRelationRepository


@dataclass(frozen=True)
class TaskPresentationData:
    """タスクレスポンス整形に必要な表示補助データ。"""

    requirements_by_task_id: dict[int, list[dict[str, Any]]]
    blocked_task_ids: set[int]


class TaskPresentationDataService:
    """タスクレスポンス用の表示補助データ取得を担当する。"""

    def __init__(
        self,
        *,
        task_repository: TaskRepository | None = None,
        relation_repository: RequirementTaskRelationRepository | None = None,
        dependency_repository: TaskDependencyRepository | None = None,
    ) -> None:
        """TaskPresentationDataServiceを初期化する。"""
        self.task_repository = task_repository or TaskRepository()
        self.relation_repository = (
            relation_repository or RequirementTaskRelationRepository()
        )
        self.dependency_repository = (
            dependency_repository or TaskDependencyRepository()
        )

    def get_task_presentation_data(
        self,
        db: Session,
        tasks: list[Task],
    ) -> TaskPresentationData:
        """タスクレスポンス整形に必要な表示補助データを取得する。

        Args:
            db: DBセッション。
            tasks: レスポンスへ変換するタスク一覧。

        Returns:
            タスクレスポンス整形に必要な表示補助データ。
        """
        task_ids = [task.id for task in tasks]
        requirements_by_task_id = (
            self.relation_repository.list_requirement_summaries_by_task_ids(
                db,
                task_ids,
            )
        )
        blocked_task_ids = {
            task.id
            for task in tasks
            if self.is_blocked(db, task)
        }
        return TaskPresentationData(
            requirements_by_task_id=requirements_by_task_id,
            blocked_task_ids=blocked_task_ids,
        )

    def is_blocked(self, db: Session, task: Task) -> bool:
        """タスクが依存関係によりブロックされているか判定する。"""
        dependencies = self.dependency_repository.list_by_task(db, task.id)
        return any(
            self._is_unfinished_predecessor(db, task, dependency)
            for dependency in dependencies
        )

    def _is_unfinished_predecessor(
        self,
        db: Session,
        task: Task,
        dependency: TaskDependency,
    ) -> bool:
        """依存関係が未完了の先行タスクによるブロックか判定する。"""
        if (
            dependency.successor_task_id != task.id
            or dependency.dependency_type != "finish_to_start"
        ):
            return False
        predecessor = self.task_repository.get_by_id(
            db,
            dependency.predecessor_task_id,
        )
        return predecessor is not None and predecessor.status != "done"
