"""タスク依存関係のビジネスロジックを提供するService。"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.task import Task, TaskDependency
from app.repositories.task_change_log import TaskChangeLogRepository
from app.repositories.task_dependency import TaskDependencyRepository
from app.repositories.task_item import TaskRepository
from app.schemas.task import TaskDependencyCreate, TaskDependencyUpdate
from app.services.conflict import (
    build_conflict_current,
    raise_duplicate_after_rollback,
    raise_if_version_conflict,
)

TASK_DEPENDENCY_CONFLICT_CURRENT_FIELDS = (
    "id",
    "predecessor_task_id",
    "successor_task_id",
    "dependency_type",
    "lag_days",
    "version",
    "created_by",
    "created_at",
    "updated_at",
)


class TaskDependencyAction:
    """タスク依存関係変更履歴の操作種別定数。"""

    CREATED = "dependency.created"
    UPDATED = "dependency.updated"
    DELETED = "dependency.deleted"


class TaskDependencyService:
    """タスク依存関係のビジネスロジックを提供する。"""

    def __init__(
        self,
        *,
        task_repository: TaskRepository | None = None,
        dependency_repository: TaskDependencyRepository | None = None,
        change_log_repository: TaskChangeLogRepository | None = None,
    ) -> None:
        """TaskDependencyServiceを初期化する。"""
        self.task_repository = task_repository or TaskRepository()
        self.dependency_repository = (
            dependency_repository or TaskDependencyRepository()
        )
        self.change_log_repository = change_log_repository or TaskChangeLogRepository()

    def create_dependency(
        self,
        db: Session,
        *,
        dependency_in: TaskDependencyCreate,
        actor_id: int | None,
    ) -> TaskDependency:
        """タスク依存関係を作成する。"""
        predecessor = self._get_task(db, dependency_in.predecessor_task_id)
        successor = self._get_task(db, dependency_in.successor_task_id)
        self._validate_dependency(predecessor, successor, dependency_in.dependency_type)
        if self._creates_cycle(db, predecessor.id, successor.id):
            raise BadRequestError(error_messages.TASK_DEPENDENCY_CYCLE)
        try:
            dependency = self.dependency_repository.create(
                db,
                dependency_in=dependency_in,
                actor_id=actor_id,
            )
        except IntegrityError as exc:
            raise_duplicate_after_rollback(
                db,
                error_messages.DUPLICATE_RESOURCE,
                exc,
            )
        self._record_dependency_change(
            db,
            project_id=successor.project_id,
            dependency=dependency,
            action=TaskDependencyAction.CREATED,
            new_value={
                "predecessor_task_id": predecessor.id,
                "successor_task_id": successor.id,
                "dependency_type": dependency.dependency_type,
            },
            changed_by=actor_id,
        )
        return dependency

    def list_dependencies(self, db: Session, task_id: int) -> list[TaskDependency]:
        """対象タスクに関係する依存関係一覧を取得する。"""
        self._get_task(db, task_id)
        return self.dependency_repository.list_by_task(db, task_id)

    def get_dependency(self, db: Session, dependency_id: int) -> TaskDependency:
        """タスク依存関係を取得する。"""
        dependency = self.dependency_repository.get_by_id(db, dependency_id)
        if dependency is None:
            raise NotFoundError(error_messages.TASK_DEPENDENCY_NOT_FOUND)
        return dependency

    def update_dependency(
        self,
        db: Session,
        *,
        dependency_id: int,
        dependency_in: TaskDependencyUpdate,
        actor_id: int | None,
    ) -> TaskDependency:
        """タスク依存関係を更新する。"""
        dependency = self.get_dependency(db, dependency_id)
        successor = self._get_task(db, dependency.successor_task_id)
        self._raise_if_dependency_version_conflict(
            dependency,
            dependency_in.version,
        )
        dependency = self.dependency_repository.update(
            db,
            dependency=dependency,
            dependency_in=dependency_in,
        )
        self._record_dependency_change(
            db,
            project_id=successor.project_id,
            dependency=dependency,
            action=TaskDependencyAction.UPDATED,
            changed_by=actor_id,
        )
        return dependency

    def delete_dependency(
        self,
        db: Session,
        *,
        dependency_id: int,
        actor_id: int | None,
    ) -> None:
        """タスク依存関係を削除する。"""
        dependency = self.get_dependency(db, dependency_id)
        successor = self._get_task(db, dependency.successor_task_id)
        self.dependency_repository.delete(db, dependency)
        self._record_dependency_change(
            db,
            project_id=successor.project_id,
            dependency_id=dependency_id,
            action=TaskDependencyAction.DELETED,
            changed_by=actor_id,
        )

    def _get_task(self, db: Session, task_id: int) -> Task:
        """タスクを取得する。"""
        task = self.task_repository.get_by_id(db, task_id)
        if task is None:
            raise NotFoundError(error_messages.TASK_NOT_FOUND)
        return task

    def _validate_dependency(
        self,
        predecessor: Task,
        successor: Task,
        dependency_type: str,
    ) -> None:
        """タスク依存関係の基本制約を検証する。"""
        if predecessor.id == successor.id:
            raise BadRequestError(error_messages.TASK_DEPENDENCY_INVALID)
        if predecessor.project_id != successor.project_id:
            raise BadRequestError(error_messages.TASK_DEPENDENCY_INVALID)
        if dependency_type != "finish_to_start":
            raise BadRequestError(error_messages.TASK_DEPENDENCY_INVALID)
        if (
            predecessor.parent_task_id == successor.id
            or successor.parent_task_id == predecessor.id
        ):
            raise BadRequestError(error_messages.TASK_DEPENDENCY_INVALID)

    def _creates_cycle(
        self,
        db: Session,
        predecessor_task_id: int,
        successor_task_id: int,
    ) -> bool:
        """依存関係追加で循環が発生するか判定する。"""
        stack = [successor_task_id]
        visited: set[int] = set()
        while stack:
            current_task_id = stack.pop()
            if current_task_id == predecessor_task_id:
                return True
            if current_task_id in visited:
                continue
            visited.add(current_task_id)
            stack.extend(
                dependency.successor_task_id
                for dependency in self.dependency_repository.list_successors(
                    db,
                    current_task_id,
                )
            )
        return False

    def _raise_if_dependency_version_conflict(
        self,
        dependency: TaskDependency,
        requested_version: int,
    ) -> None:
        """依存関係更新時の楽観的排他制御を検証する。"""
        raise_if_version_conflict(
            current_version=dependency.version,
            requested_version=requested_version,
            current=build_conflict_current(
                dependency,
                TASK_DEPENDENCY_CONFLICT_CURRENT_FIELDS,
            ),
        )

    def _record_dependency_change(
        self,
        db: Session,
        *,
        project_id: int,
        action: str,
        dependency: TaskDependency | None = None,
        dependency_id: int | None = None,
        new_value: dict | None = None,
        changed_by: int | None = None,
    ) -> None:
        """タスク依存関係変更履歴を記録する。"""
        target_id = dependency.id if dependency is not None else dependency_id
        if target_id is None:
            raise ValueError("target_id is required")
        self.change_log_repository.create(
            db,
            project_id=project_id,
            target_type="dependency",
            target_id=target_id,
            action=action,
            new_value=new_value,
            changed_by=changed_by,
        )
