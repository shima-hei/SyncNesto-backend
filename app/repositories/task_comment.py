"""タスク管理Repositoryを定義するモジュール。"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.task import (
    TaskComment,
)


class TaskCommentRepository:
    """TaskCommentテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        task_id: int,
        parent_comment_id: int | None,
        body: str,
        actor_id: int | None,
    ) -> TaskComment:
        """タスクコメントを作成する。"""
        comment = TaskComment(
            task_id=task_id,
            parent_comment_id=parent_comment_id,
            body=body,
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(comment)
        db.commit()
        db.refresh(comment)
        return comment

    def get_by_id(self, db: Session, comment_id: int) -> TaskComment | None:
        """idに一致する未削除タスクコメントを取得する。"""
        return (
            db.query(TaskComment)
            .filter(TaskComment.id == comment_id, TaskComment.deleted_at.is_(None))
            .first()
        )

    def list_by_ids_including_deleted(
        self,
        db: Session,
        comment_ids: list[int],
    ) -> list[TaskComment]:
        """id一覧に一致するタスクコメントを論理削除済みも含めて取得する。"""
        if not comment_ids:
            return []
        return db.query(TaskComment).filter(TaskComment.id.in_(comment_ids)).all()

    def list_by_task(self, db: Session, task_id: int) -> list[TaskComment]:
        """タスクに紐づく未削除コメント一覧を取得する。"""
        return (
            db.query(TaskComment)
            .filter(TaskComment.task_id == task_id, TaskComment.deleted_at.is_(None))
            .order_by(TaskComment.created_at, TaskComment.id)
            .all()
        )

    def update_body(
        self,
        db: Session,
        *,
        comment: TaskComment,
        body: str,
        actor_id: int | None,
    ) -> TaskComment:
        """タスクコメント本文を更新する。"""
        comment.body = body
        comment.updated_by = actor_id
        comment.version += 1
        db.commit()
        db.refresh(comment)
        return comment

    def set_resolved(
        self,
        db: Session,
        *,
        comment: TaskComment,
        is_resolved: bool,
        actor_id: int | None,
    ) -> TaskComment:
        """タスクコメントの解決状態を更新する。"""
        comment.is_resolved = is_resolved
        comment.updated_by = actor_id
        comment.version += 1
        db.commit()
        db.refresh(comment)
        return comment

    def soft_delete(
        self,
        db: Session,
        *,
        comment: TaskComment,
        actor_id: int | None,
    ) -> TaskComment:
        """タスクコメントを論理削除する。"""
        comment.deleted_at = datetime.now(UTC)
        comment.updated_by = actor_id
        comment.version += 1
        db.commit()
        db.refresh(comment)
        return comment
