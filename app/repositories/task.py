"""タスク管理Repositoryを定義するモジュール。"""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.models.requirement import Requirement, RequirementDocument
from app.models.task import (
    Board,
    BoardColumn,
    Milestone,
    RequirementTaskRelation,
    Task,
    TaskChangeLog,
    TaskComment,
    TaskDependency,
)
from app.schemas.task import (
    BoardColumnCreate,
    BoardColumnUpdate,
    BoardCreate,
    BoardUpdate,
    MilestoneCreate,
    MilestoneUpdate,
    TaskCreate,
    TaskDependencyCreate,
    TaskDependencyUpdate,
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


class RequirementTaskRelationRepository:
    """RequirementTaskRelationテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        requirement_id: int,
        task_id: int,
        relation_type: str,
        actor_id: int | None,
    ) -> RequirementTaskRelation:
        """要件タスク関連を作成する。"""
        relation = RequirementTaskRelation(
            requirement_id=requirement_id,
            task_id=task_id,
            relation_type=relation_type,
            created_by=actor_id,
        )
        db.add(relation)
        db.commit()
        db.refresh(relation)
        return relation

    def get_by_id(
        self,
        db: Session,
        relation_id: int,
    ) -> RequirementTaskRelation | None:
        """idに一致する要件タスク関連を取得する。"""
        return (
            db.query(RequirementTaskRelation)
            .filter(RequirementTaskRelation.id == relation_id)
            .first()
        )

    def list_by_requirement(
        self,
        db: Session,
        requirement_id: int,
    ) -> list[RequirementTaskRelation]:
        """要件に紐づくタスク関連一覧を取得する。"""
        return (
            db.query(RequirementTaskRelation)
            .filter(RequirementTaskRelation.requirement_id == requirement_id)
            .order_by(RequirementTaskRelation.id)
            .all()
        )

    def list_tasks_by_requirement(
        self,
        db: Session,
        requirement_id: int,
    ) -> list[Task]:
        """要件に紐づく未削除タスク一覧を取得する。"""
        return (
            db.query(Task)
            .join(RequirementTaskRelation, RequirementTaskRelation.task_id == Task.id)
            .filter(
                RequirementTaskRelation.requirement_id == requirement_id,
                Task.deleted_at.is_(None),
            )
            .order_by(Task.sort_order, Task.id)
            .all()
        )

    def list_requirement_summaries_by_task_ids(
        self,
        db: Session,
        task_ids: list[int],
    ) -> dict[int, list[dict[str, Any]]]:
        """タスクIDごとの関連要件概要を取得する。

        Args:
            db: DBセッション。
            task_ids: タスクID一覧。

        Returns:
            task_idをキー、関連要件概要一覧を値にした辞書。
        """
        if not task_ids:
            return {}
        rows = (
            db.query(
                RequirementTaskRelation.task_id,
                RequirementTaskRelation.id.label("relation_id"),
                RequirementTaskRelation.relation_type,
                Requirement.id.label("requirement_id"),
                Requirement.requirement_code,
                Requirement.title,
            )
            .join(Requirement, RequirementTaskRelation.requirement_id == Requirement.id)
            .filter(
                RequirementTaskRelation.task_id.in_(task_ids),
                Requirement.deleted_at.is_(None),
            )
            .order_by(RequirementTaskRelation.id)
            .all()
        )
        summaries: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            summaries.setdefault(row.task_id, []).append(
                {
                    "id": row.requirement_id,
                    "requirement_code": row.requirement_code,
                    "title": row.title,
                    "relation_id": row.relation_id,
                    "relation_type": row.relation_type,
                }
            )
        return summaries

    def delete(self, db: Session, relation: RequirementTaskRelation) -> None:
        """要件タスク関連を削除する。"""
        db.delete(relation)
        db.commit()


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


class BoardRepository:
    """Boardテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        project_id: int,
        board_in: BoardCreate,
        actor_id: int | None,
    ) -> Board:
        """ボードを作成する。"""
        board = Board(
            project_id=project_id,
            name=board_in.name,
            description=board_in.description,
            board_type=board_in.board_type,
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(board)
        db.commit()
        db.refresh(board)
        return board

    def get_by_id(self, db: Session, board_id: int) -> Board | None:
        """idに一致する未削除ボードを取得する。"""
        return (
            db.query(Board)
            .filter(Board.id == board_id, Board.deleted_at.is_(None))
            .first()
        )

    def list_by_project(self, db: Session, project_id: int) -> list[Board]:
        """プロジェクト内ボード一覧を取得する。"""
        return (
            db.query(Board)
            .filter(Board.project_id == project_id, Board.deleted_at.is_(None))
            .order_by(Board.id)
            .all()
        )

    def update(
        self,
        db: Session,
        *,
        board: Board,
        board_in: BoardUpdate,
        actor_id: int | None,
    ) -> Board:
        """ボードを更新する。"""
        for field in ["name", "description", "board_type"]:
            if field in board_in.model_fields_set:
                setattr(board, field, getattr(board_in, field))
        board.updated_by = actor_id
        board.version += 1
        db.commit()
        db.refresh(board)
        return board

    def soft_delete(self, db: Session, *, board: Board, actor_id: int | None) -> Board:
        """ボードを論理削除する。"""
        board.deleted_at = datetime.now(UTC)
        board.updated_by = actor_id
        board.version += 1
        db.commit()
        db.refresh(board)
        return board


class BoardColumnRepository:
    """BoardColumnテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        board_id: int,
        column_in: BoardColumnCreate,
    ) -> BoardColumn:
        """ボード列を作成する。"""
        column = BoardColumn(
            board_id=board_id,
            name=column_in.name,
            status_key=column_in.status_key,
            sort_order=column_in.sort_order,
            wip_limit=column_in.wip_limit,
            is_done_column=column_in.is_done_column,
        )
        db.add(column)
        db.commit()
        db.refresh(column)
        return column

    def get_by_id(self, db: Session, column_id: int) -> BoardColumn | None:
        """idに一致する未削除ボード列を取得する。"""
        return (
            db.query(BoardColumn)
            .filter(BoardColumn.id == column_id, BoardColumn.deleted_at.is_(None))
            .first()
        )

    def list_by_board(self, db: Session, board_id: int) -> list[BoardColumn]:
        """ボード列一覧を取得する。"""
        return (
            db.query(BoardColumn)
            .filter(BoardColumn.board_id == board_id, BoardColumn.deleted_at.is_(None))
            .order_by(BoardColumn.sort_order, BoardColumn.id)
            .all()
        )

    def update(
        self,
        db: Session,
        *,
        column: BoardColumn,
        column_in: BoardColumnUpdate,
    ) -> BoardColumn:
        """ボード列を更新する。"""
        for field in [
            "name",
            "status_key",
            "sort_order",
            "wip_limit",
            "is_done_column",
        ]:
            if field in column_in.model_fields_set:
                setattr(column, field, getattr(column_in, field))
        column.version += 1
        db.commit()
        db.refresh(column)
        return column

    def soft_delete(self, db: Session, column: BoardColumn) -> BoardColumn:
        """ボード列を論理削除する。"""
        column.deleted_at = datetime.now(UTC)
        column.version += 1
        db.commit()
        db.refresh(column)
        return column


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


class TaskRequirementLookupRepository:
    """タスク機能から要件と要件定義書を参照するRepository。"""

    def get_requirement(self, db: Session, requirement_id: int) -> Requirement | None:
        """idに一致する未削除要件を取得する。"""
        return (
            db.query(Requirement)
            .filter(Requirement.id == requirement_id, Requirement.deleted_at.is_(None))
            .first()
        )

    def list_requirements_by_ids(
        self,
        db: Session,
        requirement_ids: list[int],
    ) -> list[Requirement]:
        """id一覧に一致する未削除要件を取得する。

        Args:
            db: DBセッション。
            requirement_ids: 取得対象の要件ID一覧。

        Returns:
            未削除要件一覧。
        """
        if not requirement_ids:
            return []
        return (
            db.query(Requirement)
            .filter(
                Requirement.id.in_(requirement_ids),
                Requirement.deleted_at.is_(None),
            )
            .all()
        )

    def get_requirement_project_id(
        self,
        db: Session,
        requirement_id: int,
    ) -> int | None:
        """要件が属するプロジェクトIDを取得する。"""
        row = (
            db.query(RequirementDocument.project_id)
            .join(Requirement, Requirement.document_id == RequirementDocument.id)
            .filter(
                Requirement.id == requirement_id,
                Requirement.deleted_at.is_(None),
                RequirementDocument.deleted_at.is_(None),
            )
            .first()
        )
        return row[0] if row is not None else None
