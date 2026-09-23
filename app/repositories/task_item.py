"""タスク管理Repositoryを定義するモジュール。"""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.models.task import (
    RequirementTaskRelation,
    Task,
)
from app.schemas.task import (
    TaskCreate,
    TaskUpdate,
)


class TaskRepository:
    """Taskテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        project_id: int,
        task_in: TaskCreate,
        actor_id: int | None,
    ) -> Task:
        """タスクを作成する。"""
        if task_in.task_code is None:
            raise ValueError("task_code is required")
        task = Task(
            project_id=project_id,
            parent_task_id=task_in.parent_task_id,
            task_code=task_in.task_code,
            title=task_in.title,
            description=task_in.description,
            task_type=task_in.task_type,
            status=task_in.status,
            priority=task_in.priority,
            assignee_id=task_in.assignee_id,
            reporter_id=task_in.reporter_id,
            start_date=task_in.start_date,
            due_date=task_in.due_date,
            actual_start_date=task_in.actual_start_date,
            actual_end_date=task_in.actual_end_date,
            progress_percent=task_in.progress_percent,
            estimated_minutes=task_in.estimated_minutes,
            actual_minutes=task_in.actual_minutes,
            sort_order=task_in.sort_order,
            tags=task_in.tags,
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    def get_by_id(self, db: Session, task_id: int) -> Task | None:
        """idに一致する未削除タスクを取得する。"""
        return (
            db.query(Task)
            .filter(Task.id == task_id, Task.deleted_at.is_(None))
            .first()
        )

    def list_by_ids(self, db: Session, task_ids: list[int]) -> list[Task]:
        """id一覧に一致する未削除タスクを取得する。

        Args:
            db: DBセッション。
            task_ids: 取得対象のタスクID一覧。

        Returns:
            未削除タスク一覧。
        """
        if not task_ids:
            return []
        return (
            db.query(Task)
            .filter(Task.id.in_(task_ids), Task.deleted_at.is_(None))
            .all()
        )

    def get_by_project_code(
        self,
        db: Session,
        *,
        project_id: int,
        task_code: str,
    ) -> Task | None:
        """project_idとtask_codeに一致する未削除タスクを取得する。"""
        return (
            db.query(Task)
            .filter(
                Task.project_id == project_id,
                Task.task_code == task_code,
                Task.deleted_at.is_(None),
            )
            .first()
        )

    def get_max_auto_task_number(self, db: Session, project_id: int) -> int:
        """プロジェクト内の自動採番タスクコード最大番号を取得する。

        Args:
            db: DBセッション。
            project_id: プロジェクトID。

        Returns:
            `TASK-001` 形式の最大番号。存在しない場合は0。
        """
        result = db.execute(
            text(
                """
                SELECT COALESCE(
                    MAX(CAST(substring(task_code from '^TASK-(\\d+)$') AS INTEGER)),
                    0
                )
                FROM tasks
                WHERE project_id = :project_id
                  AND task_code ~ '^TASK-[0-9]+$'
                """
            ),
            {"project_id": project_id},
        ).scalar_one()
        return int(result)

    def list_paginated(
        self,
        db: Session,
        *,
        project_id: int,
        page: int,
        page_size: int,
        status: str | None = None,
        assignee_id: int | None = None,
        requirement_id: int | None = None,
        parent_task_id: int | None = None,
        root_only: bool | None = None,
        start_date_from: date | None = None,
        due_date_to: date | None = None,
        overdue: bool | None = None,
        task_type: str | None = None,
        priority: str | None = None,
        tag: str | None = None,
        sort: str | None = None,
        q: str | None = None,
    ) -> tuple[list[Task], int]:
        """プロジェクト内タスク一覧をページング付きで取得する。"""
        query = db.query(Task).filter(
            Task.project_id == project_id,
            Task.deleted_at.is_(None),
        )
        if requirement_id is not None:
            query = query.join(
                RequirementTaskRelation,
                RequirementTaskRelation.task_id == Task.id,
            ).filter(RequirementTaskRelation.requirement_id == requirement_id)
        if parent_task_id is not None:
            query = query.filter(Task.parent_task_id == parent_task_id)
        if root_only:
            query = query.filter(Task.parent_task_id.is_(None))
        if status is not None:
            query = query.filter(Task.status == status)
        if task_type is not None:
            query = query.filter(Task.task_type == task_type)
        if priority is not None:
            query = query.filter(Task.priority == priority)
        if assignee_id is not None:
            query = query.filter(Task.assignee_id == assignee_id)
        if tag is not None:
            query = query.filter(Task.tags.contains([tag]))
        if start_date_from is not None:
            query = query.filter(Task.start_date >= start_date_from)
        if due_date_to is not None:
            query = query.filter(Task.due_date <= due_date_to)
        if overdue is not None:
            today = date.today()
            overdue_filter = (
                Task.due_date < today,
                Task.status.notin_(["done", "cancelled"]),
            )
            query = query.filter(*overdue_filter) if overdue else query.filter(
                or_(Task.due_date >= today, Task.status.in_(["done", "cancelled"]))
            )
        if q:
            like_pattern = f"%{q}%"
            query = query.filter(
                or_(
                    Task.task_code.ilike(like_pattern),
                    Task.title.ilike(like_pattern),
                    Task.description.ilike(like_pattern),
                )
            )
        total = query.count()
        order_by = self._build_order_by(sort)
        tasks = (
            query.order_by(*order_by)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return tasks, total

    def _build_order_by(self, sort: str | None) -> list[Any]:
        """タスク一覧のソート条件を作成する。

        Args:
            sort: ソート指定。

        Returns:
            SQLAlchemyのorder_byに渡す条件一覧。
        """
        sort_map: dict[str, list[Any]] = {
            "updated_desc": [Task.updated_at.desc(), Task.id.desc()],
            "updated_asc": [Task.updated_at.asc(), Task.id.asc()],
            "code_asc": [Task.task_code.asc(), Task.id.asc()],
            "code_desc": [Task.task_code.desc(), Task.id.desc()],
            "due_date_asc": [Task.due_date.asc().nullslast(), Task.id.asc()],
            "due_date_desc": [Task.due_date.desc().nullslast(), Task.id.desc()],
            "progress_desc": [Task.progress_percent.desc(), Task.id.desc()],
        }
        return sort_map.get(sort or "", [Task.sort_order.asc(), Task.id.asc()])

    def list_tags(self, db: Session, project_id: int) -> list[str]:
        """プロジェクト内の未削除タスクで使われているタグを取得する。"""
        rows = db.execute(
            text(
                """
                SELECT DISTINCT tag
                FROM tasks, jsonb_array_elements_text(tasks.tags) AS task_tag(tag)
                WHERE tasks.project_id = :project_id
                  AND tasks.deleted_at IS NULL
                  AND tag <> ''
                ORDER BY tag ASC
                """
            ),
            {"project_id": project_id},
        )
        return [row[0] for row in rows]

    def list_for_gantt(
        self,
        db: Session,
        *,
        project_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
        requirement_id: int | None = None,
        assignee_id: int | None = None,
    ) -> list[Task]:
        """ガントチャート用タスク一覧を取得する。"""
        query = db.query(Task).filter(
            Task.project_id == project_id,
            Task.deleted_at.is_(None),
        )
        if requirement_id is not None:
            query = query.join(
                RequirementTaskRelation,
                RequirementTaskRelation.task_id == Task.id,
            ).filter(RequirementTaskRelation.requirement_id == requirement_id)
        if assignee_id is not None:
            query = query.filter(Task.assignee_id == assignee_id)
        if start_date is not None:
            query = query.filter(
                or_(Task.due_date.is_(None), Task.due_date >= start_date)
            )
        if end_date is not None:
            query = query.filter(
                or_(Task.start_date.is_(None), Task.start_date <= end_date)
            )
        return query.order_by(Task.start_date, Task.due_date, Task.id).all()

    def update(
        self,
        db: Session,
        *,
        task: Task,
        task_in: TaskUpdate,
        actor_id: int | None,
    ) -> Task:
        """タスクを更新する。"""
        for field in [
            "parent_task_id",
            "task_code",
            "title",
            "description",
            "task_type",
            "status",
            "priority",
            "assignee_id",
            "reporter_id",
            "start_date",
            "due_date",
            "actual_start_date",
            "actual_end_date",
            "progress_percent",
            "estimated_minutes",
            "actual_minutes",
            "sort_order",
            "tags",
        ]:
            if field in task_in.model_fields_set:
                setattr(task, field, getattr(task_in, field))
        task.updated_by = actor_id
        task.version += 1
        db.commit()
        db.refresh(task)
        return task

    def move(
        self,
        db: Session,
        *,
        task: Task,
        status: str,
        sort_order: int,
        actor_id: int | None,
    ) -> Task:
        """タスクのボード上の位置を更新する。"""
        task.status = status
        task.sort_order = sort_order
        task.updated_by = actor_id
        task.version += 1
        db.commit()
        db.refresh(task)
        return task

    def soft_delete(self, db: Session, *, task: Task, actor_id: int | None) -> Task:
        """タスクを論理削除する。"""
        task.deleted_at = datetime.now(UTC)
        task.updated_by = actor_id
        task.version += 1
        db.commit()
        db.refresh(task)
        return task
