"""タスクコメントのビジネスロジックを提供するService。"""

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import NotFoundError
from app.models.task import Task, TaskComment
from app.models.user import User
from app.repositories.task_change_log import TaskChangeLogRepository
from app.repositories.task_comment import TaskCommentRepository
from app.repositories.task_item import TaskRepository
from app.repositories.user import UserRepository
from app.schemas.task import (
    TaskCommentCreate,
    TaskCommentStateUpdate,
    TaskCommentUpdate,
)
from app.services.conflict import build_conflict_current, raise_if_version_conflict

TASK_COMMENT_CONFLICT_CURRENT_FIELDS = (
    "id",
    "task_id",
    "parent_comment_id",
    "body",
    "is_resolved",
    "version",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
)


class TaskCommentAction:
    """タスクコメント変更履歴の操作種別定数。"""

    CREATED = "comment.created"
    UPDATED = "comment.updated"
    DELETED = "comment.deleted"
    RESOLVED = "comment.resolved"
    REOPENED = "comment.reopened"


class TaskCommentService:
    """タスクコメントのビジネスロジックを提供する。"""

    def __init__(
        self,
        *,
        task_repository: TaskRepository | None = None,
        comment_repository: TaskCommentRepository | None = None,
        change_log_repository: TaskChangeLogRepository | None = None,
        user_repository: UserRepository | None = None,
    ) -> None:
        """TaskCommentServiceを初期化する。"""
        self.task_repository = task_repository or TaskRepository()
        self.comment_repository = comment_repository or TaskCommentRepository()
        self.change_log_repository = change_log_repository or TaskChangeLogRepository()
        self.user_repository = user_repository or UserRepository()

    def create_comment(
        self,
        db: Session,
        *,
        task_id: int,
        comment_in: TaskCommentCreate,
        actor_id: int | None,
    ) -> TaskComment:
        """タスクコメントを作成する。"""
        task = self._get_task(db, task_id)
        self._validate_parent_comment(db, task_id, comment_in.parent_comment_id)
        comment = self.comment_repository.create(
            db,
            task_id=task_id,
            parent_comment_id=comment_in.parent_comment_id,
            body=comment_in.body,
            actor_id=actor_id,
        )
        self._record_comment_change(
            db,
            task=task,
            comment=comment,
            action=TaskCommentAction.CREATED,
            new_value={"task_id": task_id, "body": comment.body},
            changed_by=actor_id,
        )
        return comment

    def list_comments(self, db: Session, task_id: int) -> list[TaskComment]:
        """タスクコメント一覧を取得する。"""
        self._get_task(db, task_id)
        return self.comment_repository.list_by_task(db, task_id)

    def get_task_comment_users_by_id(
        self,
        db: Session,
        comments: list[TaskComment],
    ) -> dict[int, User]:
        """タスクコメントレスポンス整形に必要な投稿者ユーザーを取得する。"""
        user_ids = [comment.created_by for comment in comments]
        ids = sorted({user_id for user_id in user_ids if user_id is not None})
        users = self.user_repository.list_by_ids(db, ids)
        return {user.id: user for user in users}

    def get_comment(self, db: Session, comment_id: int) -> TaskComment:
        """タスクコメントを取得する。"""
        comment = self.comment_repository.get_by_id(db, comment_id)
        if comment is None:
            raise NotFoundError(error_messages.TASK_COMMENT_NOT_FOUND)
        return comment

    def update_comment(
        self,
        db: Session,
        *,
        comment_id: int,
        comment_in: TaskCommentUpdate,
        actor_id: int | None,
    ) -> TaskComment:
        """タスクコメントを更新する。"""
        comment = self.get_comment(db, comment_id)
        task = self._get_task(db, comment.task_id)
        self._raise_if_comment_version_conflict(comment, comment_in.version)
        old_body = comment.body
        comment = self.comment_repository.update_body(
            db,
            comment=comment,
            body=comment_in.body,
            actor_id=actor_id,
        )
        self._record_comment_change(
            db,
            task=task,
            comment=comment,
            action=TaskCommentAction.UPDATED,
            field_name="body",
            old_value={"task_id": task.id, "body": old_body},
            new_value={"task_id": task.id, "body": comment.body},
            changed_by=actor_id,
        )
        return comment

    def delete_comment(
        self,
        db: Session,
        *,
        comment_id: int,
        actor_id: int | None,
    ) -> None:
        """タスクコメントを論理削除する。"""
        comment = self.get_comment(db, comment_id)
        task = self._get_task(db, comment.task_id)
        self.comment_repository.soft_delete(
            db,
            comment=comment,
            actor_id=actor_id,
        )
        self._record_comment_change(
            db,
            task=task,
            comment=comment,
            action=TaskCommentAction.DELETED,
            old_value={"task_id": task.id},
            changed_by=actor_id,
        )

    def resolve_comment(
        self,
        db: Session,
        *,
        comment_id: int,
        comment_in: TaskCommentStateUpdate,
        actor_id: int | None,
    ) -> TaskComment:
        """タスクコメントを解決済みにする。"""
        return self._set_comment_resolved(
            db,
            comment_id=comment_id,
            comment_in=comment_in,
            is_resolved=True,
            actor_id=actor_id,
        )

    def reopen_comment(
        self,
        db: Session,
        *,
        comment_id: int,
        comment_in: TaskCommentStateUpdate,
        actor_id: int | None,
    ) -> TaskComment:
        """タスクコメントを未解決に戻す。"""
        return self._set_comment_resolved(
            db,
            comment_id=comment_id,
            comment_in=comment_in,
            is_resolved=False,
            actor_id=actor_id,
        )

    def _get_task(self, db: Session, task_id: int) -> Task:
        """タスクを取得する。"""
        task = self.task_repository.get_by_id(db, task_id)
        if task is None:
            raise NotFoundError(error_messages.TASK_NOT_FOUND)
        return task

    def _validate_parent_comment(
        self,
        db: Session,
        task_id: int,
        parent_comment_id: int | None,
    ) -> None:
        """親コメントが同じタスクに存在することを確認する。"""
        if parent_comment_id is None:
            return
        parent = self.comment_repository.get_by_id(db, parent_comment_id)
        if parent is None or parent.task_id != task_id:
            raise NotFoundError(error_messages.TASK_COMMENT_NOT_FOUND)

    def _set_comment_resolved(
        self,
        db: Session,
        *,
        comment_id: int,
        comment_in: TaskCommentStateUpdate,
        is_resolved: bool,
        actor_id: int | None,
    ) -> TaskComment:
        """タスクコメントの解決状態を更新する。"""
        comment = self.get_comment(db, comment_id)
        task = self._get_task(db, comment.task_id)
        self._raise_if_comment_version_conflict(comment, comment_in.version)
        old_value = comment.is_resolved
        comment = self.comment_repository.set_resolved(
            db,
            comment=comment,
            is_resolved=is_resolved,
            actor_id=actor_id,
        )
        self._record_comment_change(
            db,
            task=task,
            comment=comment,
            action=(
                TaskCommentAction.RESOLVED
                if is_resolved
                else TaskCommentAction.REOPENED
            ),
            field_name="is_resolved",
            old_value={"task_id": task.id, "is_resolved": old_value},
            new_value={"task_id": task.id, "is_resolved": comment.is_resolved},
            changed_by=actor_id,
        )
        return comment

    def _raise_if_comment_version_conflict(
        self,
        comment: TaskComment,
        requested_version: int,
    ) -> None:
        """コメント更新時の楽観的排他制御を検証する。"""
        raise_if_version_conflict(
            current_version=comment.version,
            requested_version=requested_version,
            current=build_conflict_current(
                comment,
                TASK_COMMENT_CONFLICT_CURRENT_FIELDS,
                extra={"created_by_user": None},
            ),
        )

    def _record_comment_change(
        self,
        db: Session,
        *,
        task: Task,
        comment: TaskComment,
        action: str,
        field_name: str | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        changed_by: int | None = None,
    ) -> None:
        """タスクコメント変更履歴を記録する。"""
        self.change_log_repository.create(
            db,
            project_id=task.project_id,
            target_type="task_comment",
            target_id=comment.id,
            action=action,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            changed_by=changed_by,
        )
