"""タスク変更履歴API用のデータ取得Service。"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import NotFoundError
from app.models.requirement import Requirement
from app.models.task import Task, TaskChangeLog, TaskComment
from app.models.user import User
from app.repositories.task_change_log import TaskChangeLogRepository
from app.repositories.task_comment import TaskCommentRepository
from app.repositories.task_item import TaskRepository
from app.repositories.task_requirement import TaskRequirementLookupRepository
from app.repositories.user import UserRepository
from app.services.task import TASK_CHANGE_LOG_FORMATTER, TaskTargetType


@dataclass(frozen=True)
class TaskChangeLogPresentationData:
    """タスク変更履歴レスポンス整形に必要な関連モデル。"""

    users_by_id: dict[int, User]
    tasks_by_id: dict[int, Task]
    requirements_by_id: dict[int, Requirement]
    comments_by_id: dict[int, TaskComment]


class TaskChangeLogService:
    """タスク変更履歴API用のデータ取得を担当する。"""

    def __init__(
        self,
        *,
        task_repository: TaskRepository | None = None,
        change_log_repository: TaskChangeLogRepository | None = None,
        comment_repository: TaskCommentRepository | None = None,
        requirement_lookup_repository: TaskRequirementLookupRepository | None = None,
        user_repository: UserRepository | None = None,
    ) -> None:
        """TaskChangeLogServiceを初期化する。"""
        self.task_repository = task_repository or TaskRepository()
        self.change_log_repository = change_log_repository or TaskChangeLogRepository()
        self.comment_repository = comment_repository or TaskCommentRepository()
        self.requirement_lookup_repository = (
            requirement_lookup_repository or TaskRequirementLookupRepository()
        )
        self.user_repository = user_repository or UserRepository()

    def list_task_change_logs(
        self,
        db: Session,
        *,
        task_id: int,
        page: int,
        page_size: int,
    ) -> tuple[list[TaskChangeLog], int]:
        """タスク変更履歴一覧を取得する。"""
        if self.task_repository.get_by_id(db, task_id) is None:
            raise NotFoundError(error_messages.TASK_NOT_FOUND)
        return self.change_log_repository.list_by_task(
            db,
            task_id=task_id,
            page=page,
            page_size=page_size,
        )

    def get_task_change_log_presentation_data(
        self,
        db: Session,
        change_logs: list[TaskChangeLog],
    ) -> TaskChangeLogPresentationData:
        """タスク変更履歴レスポンス整形に必要な関連モデルを取得する。"""
        users_by_id = self._get_change_log_user_models_by_id(
            db,
            self._collect_change_log_user_ids(change_logs),
        )
        tasks_by_id = self._get_change_log_tasks_by_id(
            db,
            self._collect_change_log_task_ids(change_logs),
        )
        requirements_by_id = self._get_change_log_requirements_by_id(
            db,
            self._collect_change_log_requirement_ids(change_logs),
        )
        comments_by_id = self._get_change_log_comments_by_id(
            db,
            self._collect_change_log_comment_ids(change_logs),
        )
        return TaskChangeLogPresentationData(
            users_by_id=users_by_id,
            tasks_by_id=tasks_by_id,
            requirements_by_id=requirements_by_id,
            comments_by_id=comments_by_id,
        )

    def _get_change_log_user_models_by_id(
        self,
        db: Session,
        user_ids: list[int | None],
    ) -> dict[int, User]:
        """変更履歴に含まれるユーザーモデルを取得する。"""
        ids = sorted({user_id for user_id in user_ids if user_id is not None})
        users = self.user_repository.list_by_ids(db, ids)
        return {user.id: user for user in users}

    def _get_change_log_tasks_by_id(
        self,
        db: Session,
        task_ids: list[int],
    ) -> dict[int, Task]:
        """変更履歴に含まれるタスク表示補助情報を取得する。"""
        ids = sorted(set(task_ids))
        return {task.id: task for task in self.task_repository.list_by_ids(db, ids)}

    def _get_change_log_requirements_by_id(
        self,
        db: Session,
        requirement_ids: list[int],
    ) -> dict[int, Requirement]:
        """変更履歴に含まれる要件表示補助情報を取得する。"""
        ids = sorted(set(requirement_ids))
        requirements = self.requirement_lookup_repository.list_requirements_by_ids(
            db,
            ids,
        )
        return {requirement.id: requirement for requirement in requirements}

    def _get_change_log_comments_by_id(
        self,
        db: Session,
        comment_ids: list[int],
    ) -> dict[int, TaskComment]:
        """変更履歴に含まれるタスクコメントを取得する。"""
        ids = sorted(set(comment_ids))
        comments = self.comment_repository.list_by_ids_including_deleted(db, ids)
        return {comment.id: comment for comment in comments}

    def _collect_change_log_user_ids(
        self,
        change_logs: list[TaskChangeLog],
    ) -> list[int | None]:
        """変更履歴レスポンス整形に必要なユーザーIDを集める。"""
        user_ids: list[int | None] = [log.changed_by for log in change_logs]
        user_ids.extend(
            TASK_CHANGE_LOG_FORMATTER.collect_user_ids(
                [
                    (log.field_name, log.old_value)
                    for log in change_logs
                ]
                + [
                    (log.field_name, log.new_value)
                    for log in change_logs
                ]
            )
        )
        return user_ids

    def _collect_change_log_comment_ids(
        self,
        change_logs: list[TaskChangeLog],
    ) -> list[int]:
        """変更履歴レスポンス整形に必要なコメントIDを集める。"""
        return [
            log.target_id
            for log in change_logs
            if log.target_type == TaskTargetType.COMMENT
        ]

    def _collect_change_log_task_ids(
        self,
        change_logs: list[TaskChangeLog],
    ) -> list[int]:
        """変更履歴レスポンス整形に必要なタスクIDを集める。"""
        task_ids: list[int] = []
        for log in change_logs:
            for value in (log.old_value, log.new_value):
                if value is None:
                    continue
                task_id = value.get("parent_task_id")
                if isinstance(task_id, int):
                    task_ids.append(task_id)
        return task_ids

    def _collect_change_log_requirement_ids(
        self,
        change_logs: list[TaskChangeLog],
    ) -> list[int]:
        """変更履歴レスポンス整形に必要な要件IDを集める。"""
        requirement_ids: list[int] = []
        for log in change_logs:
            for value in (log.old_value, log.new_value):
                if value is None:
                    continue
                requirement_ids.extend(
                    self._extract_requirement_ids(value.get("requirements"))
                )
        return requirement_ids

    def _extract_requirement_ids(self, value: Any) -> list[int]:
        """変更履歴値から要件IDを抽出する。"""
        if isinstance(value, int):
            return [value]
        if isinstance(value, list):
            requirement_ids: list[int] = []
            for item in value:
                requirement_ids.extend(self._extract_requirement_ids(item))
            return requirement_ids
        if isinstance(value, dict):
            for key in ("id", "requirement_id"):
                requirement_id = value.get(key)
                if isinstance(requirement_id, int):
                    return [requirement_id]
            return self._extract_requirement_ids(value.get("requirements"))
        return []
