"""タスク管理Repositoryを定義するモジュール。"""


from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.task import (
    TaskChangeLog,
    TaskComment,
)


class TaskChangeLogRepository:
    """TaskChangeLogテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        project_id: int,
        target_type: str,
        target_id: int,
        action: str,
        field_name: str | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        reason: str | None = None,
        changed_by: int | None = None,
    ) -> TaskChangeLog:
        """タスク変更履歴を作成する。"""
        change_log = TaskChangeLog(
            project_id=project_id,
            target_type=target_type,
            target_id=target_id,
            action=action,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            changed_by=changed_by,
        )
        db.add(change_log)
        db.commit()
        db.refresh(change_log)
        return change_log

    def list_by_task(
        self,
        db: Session,
        *,
        task_id: int,
        page: int,
        page_size: int,
    ) -> tuple[list[TaskChangeLog], int]:
        """タスク変更履歴をページング付きで取得する。

        Args:
            db: DBセッション。
            task_id: タスクID。
            page: ページ番号。
            page_size: 1ページあたりの件数。

        Returns:
            変更履歴一覧と総件数。
        """
        comment_ids = select(TaskComment.id).where(TaskComment.task_id == task_id)
        query = db.query(TaskChangeLog).filter(
            or_(
                (TaskChangeLog.target_type == "task")
                & (TaskChangeLog.target_id == task_id),
                (TaskChangeLog.target_type == "task_comment")
                & (TaskChangeLog.target_id.in_(comment_ids)),
            )
        )
        total = query.count()
        items = (
            query.order_by(TaskChangeLog.changed_at.desc(), TaskChangeLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total
