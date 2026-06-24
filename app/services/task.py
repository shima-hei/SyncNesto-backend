"""タスク管理のビジネスロジックを提供するモジュール。"""

from datetime import date
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import (
    BadRequestError,
    DuplicateResourceError,
    ForbiddenError,
    NotFoundError,
)
from app.models.requirement import Requirement
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
from app.models.user import User
from app.repositories.project import ProjectRepository
from app.repositories.task import (
    BoardColumnRepository,
    BoardRepository,
    MilestoneRepository,
    RequirementTaskRelationRepository,
    TaskChangeLogRepository,
    TaskCommentRepository,
    TaskDependencyRepository,
    TaskRepository,
    TaskRequirementLookupRepository,
)
from app.repositories.user import UserRepository
from app.schemas.change_log import ChangeLogUserRead
from app.schemas.task import (
    BoardColumnCreate,
    BoardColumnRead,
    BoardColumnUpdate,
    BoardCreate,
    BoardRead,
    BoardUpdate,
    MilestoneCreate,
    MilestoneRead,
    MilestoneUpdate,
    RequirementTaskProgressRead,
    TaskChangeLogRead,
    TaskCommentCreate,
    TaskCommentRead,
    TaskCommentStateUpdate,
    TaskCommentUpdate,
    TaskCreate,
    TaskDependencyCreate,
    TaskDependencyRead,
    TaskDependencyUpdate,
    TaskMoveRequest,
    TaskRead,
    TaskRequirementSummary,
    TaskUpdate,
)
from app.services.audit_log import AuditLogService
from app.services.authorization import AuthorizationService
from app.services.change_log_formatter import (
    ChangeLogFormatConfig,
    ChangeLogFormatter,
    build_changed_field_snapshots,
)
from app.services.conflict import (
    raise_duplicate_after_rollback,
    raise_if_version_conflict,
)

AUTO_TASK_CODE_PREFIX = "TASK"
AUTO_TASK_CODE_RETRY_LIMIT = 5
TASK_UPDATABLE_FIELDS = {
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
}

TASK_CHANGE_LOG_ACTION_MAP = {
    "status.changed": "status_changed",
    "assignee.changed": "assignee_changed",
    "schedule.changed": "schedule_changed",
    "progress.changed": "progress_changed",
    "comment.created": "comment_created",
    "comment.updated": "comment_updated",
    "comment.deleted": "comment_deleted",
    "comment.resolved": "comment_resolved",
    "comment.reopened": "comment_reopened",
    "relation.created": "updated",
    "relation.deleted": "updated",
    "dependency.created": "updated",
    "dependency.updated": "updated",
    "dependency.deleted": "updated",
    "moved": "updated",
}
TASK_STATUS_LABELS = {
    "backlog": "バックログ",
    "todo": "未着手",
    "in_progress": "作業中",
    "in_review": "レビュー中",
    "done": "完了",
    "blocked": "ブロック中",
    "cancelled": "中止",
}
TASK_PRIORITY_LABELS = {
    "critical": "緊急",
    "high": "高",
    "medium": "中",
    "low": "低",
}
TASK_TYPE_LABELS = {
    "frontend": "フロントエンド",
    "backend": "バックエンド",
    "database": "データベース",
    "infrastructure": "インフラ",
    "security": "セキュリティ",
    "test": "テスト",
    "review": "レビュー",
    "investigation": "調査",
    "documentation": "ドキュメント",
    "other": "その他",
}
TASK_CHANGE_LOG_FORMATTER = ChangeLogFormatter(
    ChangeLogFormatConfig(
        action_map=TASK_CHANGE_LOG_ACTION_MAP,
        target_type_map={
            "task": "task",
            "task_comment": "task_comment",
        },
        field_names={
            "task_code",
            "title",
            "description",
            "status",
            "priority",
            "task_type",
            "assignee_id",
            "reporter_id",
            "start_date",
            "due_date",
            "actual_start_date",
            "actual_end_date",
            "estimated_minutes",
            "actual_minutes",
            "progress_percent",
            "parent_task_id",
            "sort_order",
            "tags",
            "requirements",
            "body",
            "is_resolved",
        },
        field_value_labels={
            "status": TASK_STATUS_LABELS,
            "priority": TASK_PRIORITY_LABELS,
            "task_type": TASK_TYPE_LABELS,
        },
        user_id_fields={"assignee_id", "reporter_id"},
    )
)


class TaskChangeLogAction:
    """タスク変更履歴の操作種別定数。"""

    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    STATUS_CHANGED = "status.changed"
    ASSIGNEE_CHANGED = "assignee.changed"
    SCHEDULE_CHANGED = "schedule.changed"
    PROGRESS_CHANGED = "progress.changed"
    RELATION_CREATED = "relation.created"
    RELATION_DELETED = "relation.deleted"
    DEPENDENCY_CREATED = "dependency.created"
    DEPENDENCY_UPDATED = "dependency.updated"
    DEPENDENCY_DELETED = "dependency.deleted"
    MOVED = "moved"
    MILESTONE_CREATED = "milestone.created"
    MILESTONE_UPDATED = "milestone.updated"
    MILESTONE_DELETED = "milestone.deleted"
    BOARD_CREATED = "board.created"
    BOARD_UPDATED = "board.updated"
    BOARD_DELETED = "board.deleted"
    COLUMN_CREATED = "column.created"
    COLUMN_UPDATED = "column.updated"
    COLUMN_DELETED = "column.deleted"
    COMMENT_CREATED = "comment.created"
    COMMENT_UPDATED = "comment.updated"
    COMMENT_DELETED = "comment.deleted"
    COMMENT_RESOLVED = "comment.resolved"
    COMMENT_REOPENED = "comment.reopened"


class TaskTargetType:
    """タスク変更履歴の対象種別定数。"""

    TASK = "task"
    COMMENT = "task_comment"
    RELATION = "relation"
    DEPENDENCY = "dependency"
    BOARD = "board"
    COLUMN = "board_column"
    MILESTONE = "milestone"


class TaskService:
    """タスク管理のビジネスロジックを提供する。"""

    def __init__(
        self,
        task_repository: TaskRepository | None = None,
        relation_repository: RequirementTaskRelationRepository | None = None,
        dependency_repository: TaskDependencyRepository | None = None,
        milestone_repository: MilestoneRepository | None = None,
        board_repository: BoardRepository | None = None,
        column_repository: BoardColumnRepository | None = None,
        requirement_lookup_repository: TaskRequirementLookupRepository | None = None,
        project_repository: ProjectRepository | None = None,
        authorization_service: AuthorizationService | None = None,
        change_log_repository: TaskChangeLogRepository | None = None,
        comment_repository: TaskCommentRepository | None = None,
        user_repository: UserRepository | None = None,
        audit_log_service: AuditLogService | None = None,
    ) -> None:
        """TaskServiceを初期化する。"""
        self.task_repository = task_repository or TaskRepository()
        self.relation_repository = (
            relation_repository or RequirementTaskRelationRepository()
        )
        self.dependency_repository = (
            dependency_repository or TaskDependencyRepository()
        )
        self.milestone_repository = milestone_repository or MilestoneRepository()
        self.board_repository = board_repository or BoardRepository()
        self.column_repository = column_repository or BoardColumnRepository()
        self.requirement_lookup_repository = (
            requirement_lookup_repository or TaskRequirementLookupRepository()
        )
        self.project_repository = project_repository or ProjectRepository()
        self.authorization_service = authorization_service or AuthorizationService()
        self.change_log_repository = change_log_repository or TaskChangeLogRepository()
        self.comment_repository = comment_repository or TaskCommentRepository()
        self.user_repository = user_repository or UserRepository()
        self.audit_log_service = audit_log_service or AuditLogService()

    def create_task(
        self,
        db: Session,
        *,
        project_id: int,
        task_in: TaskCreate,
        actor_id: int | None,
    ) -> Task:
        """タスクを作成する。

        Args:
            db: DBセッション。
            project_id: 作成先プロジェクトID。
            task_in: タスク作成リクエスト。
            actor_id: 操作ユーザーID。

        Returns:
            作成されたタスク。
        """
        self._ensure_project_exists(db, project_id)
        self._validate_parent_task(db, project_id, task_in.parent_task_id)
        self._validate_task_state(task_in.status, task_in.progress_percent)
        if task_in.requirement_id is not None:
            self._ensure_requirement_in_project(db, project_id, task_in.requirement_id)
        requested_task_code = self._normalize_task_code(task_in.task_code)
        if requested_task_code and self.task_repository.get_by_project_code(
            db,
            project_id=project_id,
            task_code=requested_task_code,
        ):
            raise DuplicateResourceError(error_messages.TASK_CODE_ALREADY_EXISTS)

        normalized_dates = self._normalize_task_dates(
            status=task_in.status,
            progress_percent=task_in.progress_percent,
            actual_start_date=task_in.actual_start_date,
            actual_end_date=task_in.actual_end_date,
        )
        retry_limit = 1 if requested_task_code else AUTO_TASK_CODE_RETRY_LIMIT
        for attempt in range(retry_limit):
            task_code = requested_task_code or self._generate_task_code(db, project_id)
            normalized = task_in.model_copy(
                update={
                    **normalized_dates,
                    "task_code": task_code,
                }
            )
            try:
                task = self.task_repository.create(
                    db,
                    project_id=project_id,
                    task_in=normalized,
                    actor_id=actor_id,
                )
            except IntegrityError as exc:
                db.rollback()
                if requested_task_code or attempt == retry_limit - 1:
                    raise_duplicate_after_rollback(
                        db,
                        error_messages.TASK_CODE_ALREADY_EXISTS,
                        exc,
                    )
                continue
            self._record_change(
                db,
                project_id=project_id,
                target_type=TaskTargetType.TASK,
                target_id=task.id,
                action=TaskChangeLogAction.CREATED,
                new_value={"task_code": task.task_code, "title": task.title},
                changed_by=actor_id,
            )
            self._record_audit(
                db,
                event_type="task.created",
                actor_id=actor_id,
                project_id=project_id,
                resource_id=task.id,
                metadata={"task_code": task.task_code},
            )
            if task_in.requirement_id is not None:
                self.create_requirement_task_relation(
                    db,
                    requirement_id=task_in.requirement_id,
                    task_id=task.id,
                    relation_type=task_in.relation_type,
                    actor_id=actor_id,
                )
            return task
        raise DuplicateResourceError(error_messages.TASK_CODE_ALREADY_EXISTS)

    def list_tasks(
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
        """プロジェクト内タスク一覧を取得する。"""
        self._ensure_project_exists(db, project_id)
        if requirement_id is not None:
            self._ensure_requirement_in_project(db, project_id, requirement_id)
        return self.task_repository.list_paginated(
            db,
            project_id=project_id,
            page=page,
            page_size=page_size,
            status=status,
            assignee_id=assignee_id,
            requirement_id=requirement_id,
            start_date_from=start_date_from,
            due_date_to=due_date_to,
            overdue=overdue,
            task_type=task_type,
            priority=priority,
            tag=tag,
            sort=sort,
            q=q,
        )

    def get_task(self, db: Session, task_id: int) -> Task:
        """タスクを取得する。"""
        task = self.task_repository.get_by_id(db, task_id)
        if task is None:
            raise NotFoundError(error_messages.TASK_NOT_FOUND)
        return task

    def update_task(
        self,
        db: Session,
        *,
        task_id: int,
        task_in: TaskUpdate,
        actor_id: int | None,
    ) -> Task:
        """タスクを更新する。"""
        task = self.get_task(db, task_id)
        current = self.build_task_read(db, task).model_dump()
        raise_if_version_conflict(
            current_version=task.version,
            requested_version=task_in.version,
            current=current,
        )
        if "task_code" in task_in.model_fields_set:
            normalized_task_code = self._normalize_task_code(task_in.task_code)
            if normalized_task_code is None:
                task_in = task_in.model_copy(update={"task_code": task.task_code})
            else:
                task_in = task_in.model_copy(update={"task_code": normalized_task_code})
        if "parent_task_id" in task_in.model_fields_set:
            self._validate_parent_task(db, task.project_id, task_in.parent_task_id)
        if (
            task_in.task_code is not None
            and task_in.task_code != task.task_code
            and self.task_repository.get_by_project_code(
                db,
                project_id=task.project_id,
                task_code=task_in.task_code,
            )
        ):
            raise DuplicateResourceError(error_messages.TASK_CODE_ALREADY_EXISTS)

        next_status = task_in.status if task_in.status is not None else task.status
        next_progress = (
            task_in.progress_percent
            if task_in.progress_percent is not None
            else task.progress_percent
        )
        self._validate_task_state(next_status, next_progress)
        normalized = task_in.model_copy(
            update=self._normalize_task_dates(
                status=next_status,
                progress_percent=next_progress,
                actual_start_date=task_in.actual_start_date
                if "actual_start_date" in task_in.model_fields_set
                else task.actual_start_date,
                actual_end_date=task_in.actual_end_date
                if "actual_end_date" in task_in.model_fields_set
                else task.actual_end_date,
            )
        )
        before_snapshot = self._task_snapshot(task)
        try:
            task = self.task_repository.update(
                db,
                task=task,
                task_in=normalized,
                actor_id=actor_id,
            )
        except IntegrityError as exc:
            raise_duplicate_after_rollback(
                db,
                error_messages.TASK_CODE_ALREADY_EXISTS,
                exc,
            )

        updated_fields, old_values, new_values = build_changed_field_snapshots(
            before_snapshot,
            self._task_snapshot(task),
            TASK_UPDATABLE_FIELDS,
        )
        if updated_fields:
            self._record_task_update_logs(
                db,
                task=task,
                old_values=old_values,
                new_values=new_values,
                updated_fields=updated_fields,
                reason=task_in.change_reason,
                actor_id=actor_id,
            )
            self._record_audit(
                db,
                event_type="task.updated",
                actor_id=actor_id,
                project_id=task.project_id,
                resource_id=task.id,
                metadata={"updated_fields": updated_fields},
            )
        return task

    def delete_task(self, db: Session, *, task_id: int, actor_id: int | None) -> None:
        """タスクを論理削除する。"""
        task = self.get_task(db, task_id)
        self.task_repository.soft_delete(db, task=task, actor_id=actor_id)
        self._record_change(
            db,
            project_id=task.project_id,
            target_type=TaskTargetType.TASK,
            target_id=task.id,
            action=TaskChangeLogAction.DELETED,
            old_value={"task_code": task.task_code},
            changed_by=actor_id,
        )
        self._record_audit(
            db,
            event_type="task.deleted",
            actor_id=actor_id,
            project_id=task.project_id,
            resource_id=task.id,
            metadata={"task_code": task.task_code},
        )

    def move_task(
        self,
        db: Session,
        *,
        board_id: int,
        task_id: int,
        move_in: TaskMoveRequest,
        actor_id: int | None,
    ) -> Task:
        """ボード上のタスクを移動する。"""
        board = self.get_board(db, board_id)
        task = self.get_task(db, task_id)
        if task.project_id != board.project_id:
            raise NotFoundError(error_messages.TASK_NOT_FOUND)
        raise_if_version_conflict(
            current_version=task.version,
            requested_version=move_in.version,
            current=self.build_task_read(db, task).model_dump(),
        )
        old_values = {"status": task.status, "sort_order": task.sort_order}
        normalized = self._normalize_task_dates(
            status=move_in.status,
            progress_percent=100 if move_in.status == "done" else task.progress_percent,
            actual_start_date=task.actual_start_date,
            actual_end_date=task.actual_end_date,
        )
        if normalized["actual_start_date"] is not None:
            task.actual_start_date = normalized["actual_start_date"]
        if normalized["actual_end_date"] is not None:
            task.actual_end_date = normalized["actual_end_date"]
        if normalized["progress_percent"] == 100:
            task.progress_percent = 100
        task = self.task_repository.move(
            db,
            task=task,
            status=move_in.status,
            sort_order=move_in.sort_order,
            actor_id=actor_id,
        )
        self._record_change(
            db,
            project_id=task.project_id,
            target_type=TaskTargetType.TASK,
            target_id=task.id,
            action=TaskChangeLogAction.MOVED,
            old_value=old_values,
            new_value={"status": task.status, "sort_order": task.sort_order},
            changed_by=actor_id,
        )
        return task

    def create_requirement_task(
        self,
        db: Session,
        *,
        requirement_id: int,
        task_in: TaskCreate,
        actor_id: int | None,
    ) -> Task:
        """要件に紐づくタスクを作成する。"""
        project_id = self._get_requirement_project_id(db, requirement_id)
        return self.create_task(
            db,
            project_id=project_id,
            task_in=task_in.model_copy(update={"requirement_id": requirement_id}),
            actor_id=actor_id,
        )

    def list_requirement_tasks(self, db: Session, requirement_id: int) -> list[Task]:
        """要件に紐づくタスク一覧を取得する。"""
        self._get_requirement_project_id(db, requirement_id)
        return self.relation_repository.list_tasks_by_requirement(db, requirement_id)

    def create_requirement_task_relation(
        self,
        db: Session,
        *,
        requirement_id: int,
        task_id: int,
        relation_type: str,
        actor_id: int | None,
    ) -> RequirementTaskRelation:
        """要件タスク関連を作成する。"""
        project_id = self._get_requirement_project_id(db, requirement_id)
        task = self.get_task(db, task_id)
        if task.project_id != project_id:
            raise NotFoundError(error_messages.TASK_NOT_FOUND)
        try:
            relation = self.relation_repository.create(
                db,
                requirement_id=requirement_id,
                task_id=task_id,
                relation_type=relation_type,
                actor_id=actor_id,
            )
        except IntegrityError as exc:
            raise_duplicate_after_rollback(
                db,
                error_messages.DUPLICATE_RESOURCE,
                exc,
            )
        self._record_change(
            db,
            project_id=project_id,
            target_type=TaskTargetType.RELATION,
            target_id=relation.id,
            action=TaskChangeLogAction.RELATION_CREATED,
            new_value={
                "requirement_id": requirement_id,
                "task_id": task_id,
                "relation_type": relation_type,
            },
            changed_by=actor_id,
        )
        return relation

    def delete_requirement_task_relation(
        self,
        db: Session,
        *,
        requirement_id: int,
        relation_id: int,
        actor_id: int | None,
    ) -> None:
        """要件タスク関連を削除する。"""
        project_id = self._get_requirement_project_id(db, requirement_id)
        relation = self.relation_repository.get_by_id(db, relation_id)
        if relation is None or relation.requirement_id != requirement_id:
            raise NotFoundError(error_messages.TASK_RELATION_NOT_FOUND)
        self.relation_repository.delete(db, relation)
        self._record_change(
            db,
            project_id=project_id,
            target_type=TaskTargetType.RELATION,
            target_id=relation_id,
            action=TaskChangeLogAction.RELATION_DELETED,
            old_value={"task_id": relation.task_id},
            changed_by=actor_id,
        )

    def get_requirement_progress(
        self,
        db: Session,
        requirement_id: int,
    ) -> RequirementTaskProgressRead:
        """要件に紐づくタスクの実装進捗を取得する。"""
        tasks = self.list_requirement_tasks(db, requirement_id)
        if not tasks:
            return RequirementTaskProgressRead(
                requirement_id=requirement_id,
                task_count=0,
                progress_percent=0,
                status="not_started",
            )
        progress = round(sum(task.progress_percent for task in tasks) / len(tasks))
        statuses = {task.status for task in tasks}
        if statuses <= {"cancelled"}:
            status = "cancelled"
        elif statuses <= {"done"}:
            status = "implemented"
        elif statuses & {"in_progress", "in_review", "done"}:
            status = "in_progress"
        else:
            status = "not_started"
        return RequirementTaskProgressRead(
            requirement_id=requirement_id,
            task_count=len(tasks),
            progress_percent=progress,
            status=status,
        )

    def get_requirement_project_id(self, db: Session, requirement_id: int) -> int:
        """要件が属するプロジェクトIDを取得する。

        Args:
            db: DBセッション。
            requirement_id: 要件ID。

        Returns:
            要件が属するプロジェクトID。
        """
        return self._get_requirement_project_id(db, requirement_id)

    def create_comment(
        self,
        db: Session,
        *,
        task_id: int,
        comment_in: TaskCommentCreate,
        actor_id: int | None,
    ) -> TaskComment:
        """タスクコメントを作成する。

        Args:
            db: DBセッション。
            task_id: コメント対象タスクID。
            comment_in: コメント作成リクエスト。
            actor_id: 操作ユーザーID。

        Returns:
            作成されたタスクコメント。
        """
        task = self.get_task(db, task_id)
        self._validate_parent_comment(db, task_id, comment_in.parent_comment_id)
        comment = self.comment_repository.create(
            db,
            task_id=task_id,
            parent_comment_id=comment_in.parent_comment_id,
            body=comment_in.body,
            actor_id=actor_id,
        )
        self._record_change(
            db,
            project_id=task.project_id,
            target_type=TaskTargetType.COMMENT,
            target_id=comment.id,
            action=TaskChangeLogAction.COMMENT_CREATED,
            new_value={"task_id": task_id},
            changed_by=actor_id,
        )
        return comment

    def list_comments(self, db: Session, task_id: int) -> list[TaskComment]:
        """タスクコメント一覧を取得する。"""
        self.get_task(db, task_id)
        return self.comment_repository.list_by_task(db, task_id)

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
        task = self.get_task(db, comment.task_id)
        raise_if_version_conflict(
            current_version=comment.version,
            requested_version=comment_in.version,
            current=TaskCommentRead.model_validate(comment).model_dump(),
        )
        old_body = comment.body
        comment = self.comment_repository.update_body(
            db,
            comment=comment,
            body=comment_in.body,
            actor_id=actor_id,
        )
        self._record_change(
            db,
            project_id=task.project_id,
            target_type=TaskTargetType.COMMENT,
            target_id=comment.id,
            action=TaskChangeLogAction.COMMENT_UPDATED,
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
        task = self.get_task(db, comment.task_id)
        self.comment_repository.soft_delete(
            db,
            comment=comment,
            actor_id=actor_id,
        )
        self._record_change(
            db,
            project_id=task.project_id,
            target_type=TaskTargetType.COMMENT,
            target_id=comment.id,
            action=TaskChangeLogAction.COMMENT_DELETED,
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

    def list_task_change_logs(
        self,
        db: Session,
        *,
        task_id: int,
        page: int,
        page_size: int,
    ) -> tuple[list[TaskChangeLogRead], int]:
        """タスク変更履歴一覧を取得する。"""
        self.get_task(db, task_id)
        change_logs, total = self.change_log_repository.list_by_task(
            db,
            task_id=task_id,
            page=page,
            page_size=page_size,
        )
        users_by_id = self._get_change_log_users_by_id(
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
        return [
            self._build_task_change_log_read(
                log,
                users_by_id=users_by_id,
                tasks_by_id=tasks_by_id,
                requirements_by_id=requirements_by_id,
            )
            for log in change_logs
        ], total

    def create_dependency(
        self,
        db: Session,
        *,
        dependency_in: TaskDependencyCreate,
        actor_id: int | None,
    ) -> TaskDependency:
        """タスク依存関係を作成する。"""
        predecessor = self.get_task(db, dependency_in.predecessor_task_id)
        successor = self.get_task(db, dependency_in.successor_task_id)
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
        self._record_change(
            db,
            project_id=successor.project_id,
            target_type=TaskTargetType.DEPENDENCY,
            target_id=dependency.id,
            action=TaskChangeLogAction.DEPENDENCY_CREATED,
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
        self.get_task(db, task_id)
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
        successor = self.get_task(db, dependency.successor_task_id)
        raise_if_version_conflict(
            current_version=dependency.version,
            requested_version=dependency_in.version,
            current=TaskDependencyRead.model_validate(dependency).model_dump(),
        )
        dependency = self.dependency_repository.update(
            db,
            dependency=dependency,
            dependency_in=dependency_in,
        )
        self._record_change(
            db,
            project_id=successor.project_id,
            target_type=TaskTargetType.DEPENDENCY,
            target_id=dependency.id,
            action=TaskChangeLogAction.DEPENDENCY_UPDATED,
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
        successor = self.get_task(db, dependency.successor_task_id)
        self.dependency_repository.delete(db, dependency)
        self._record_change(
            db,
            project_id=successor.project_id,
            target_type=TaskTargetType.DEPENDENCY,
            target_id=dependency_id,
            action=TaskChangeLogAction.DEPENDENCY_DELETED,
            changed_by=actor_id,
        )

    def create_milestone(
        self,
        db: Session,
        *,
        project_id: int,
        milestone_in: MilestoneCreate,
        actor_id: int | None,
    ) -> Milestone:
        """マイルストーンを作成する。"""
        self._ensure_project_exists(db, project_id)
        milestone = self.milestone_repository.create(
            db,
            project_id=project_id,
            milestone_in=milestone_in,
            actor_id=actor_id,
        )
        self._record_change(
            db,
            project_id=project_id,
            target_type=TaskTargetType.MILESTONE,
            target_id=milestone.id,
            action=TaskChangeLogAction.MILESTONE_CREATED,
            changed_by=actor_id,
        )
        return milestone

    def list_milestones(self, db: Session, project_id: int) -> list[Milestone]:
        """プロジェクト内マイルストーン一覧を取得する。"""
        self._ensure_project_exists(db, project_id)
        return self.milestone_repository.list_by_project(db, project_id)

    def get_milestone(self, db: Session, milestone_id: int) -> Milestone:
        """マイルストーンを取得する。"""
        milestone = self.milestone_repository.get_by_id(db, milestone_id)
        if milestone is None:
            raise NotFoundError(error_messages.MILESTONE_NOT_FOUND)
        return milestone

    def update_milestone(
        self,
        db: Session,
        *,
        milestone_id: int,
        milestone_in: MilestoneUpdate,
        actor_id: int | None,
    ) -> Milestone:
        """マイルストーンを更新する。"""
        milestone = self.get_milestone(db, milestone_id)
        raise_if_version_conflict(
            current_version=milestone.version,
            requested_version=milestone_in.version,
            current=MilestoneRead.model_validate(milestone).model_dump(),
        )
        milestone = self.milestone_repository.update(
            db,
            milestone=milestone,
            milestone_in=milestone_in,
            actor_id=actor_id,
        )
        self._record_change(
            db,
            project_id=milestone.project_id,
            target_type=TaskTargetType.MILESTONE,
            target_id=milestone.id,
            action=TaskChangeLogAction.MILESTONE_UPDATED,
            changed_by=actor_id,
        )
        return milestone

    def delete_milestone(
        self,
        db: Session,
        *,
        milestone_id: int,
        actor_id: int | None,
    ) -> None:
        """マイルストーンを論理削除する。"""
        milestone = self.get_milestone(db, milestone_id)
        self.milestone_repository.soft_delete(
            db,
            milestone=milestone,
            actor_id=actor_id,
        )
        self._record_change(
            db,
            project_id=milestone.project_id,
            target_type=TaskTargetType.MILESTONE,
            target_id=milestone.id,
            action=TaskChangeLogAction.MILESTONE_DELETED,
            changed_by=actor_id,
        )

    def create_board(
        self,
        db: Session,
        *,
        project_id: int,
        board_in: BoardCreate,
        actor_id: int | None,
    ) -> Board:
        """ボードを作成する。"""
        self._ensure_project_exists(db, project_id)
        board = self.board_repository.create(
            db,
            project_id=project_id,
            board_in=board_in,
            actor_id=actor_id,
        )
        self._record_change(
            db,
            project_id=project_id,
            target_type=TaskTargetType.BOARD,
            target_id=board.id,
            action=TaskChangeLogAction.BOARD_CREATED,
            changed_by=actor_id,
        )
        return board

    def list_boards(self, db: Session, project_id: int) -> list[Board]:
        """プロジェクト内ボード一覧を取得する。"""
        self._ensure_project_exists(db, project_id)
        return self.board_repository.list_by_project(db, project_id)

    def get_board(self, db: Session, board_id: int) -> Board:
        """ボードを取得する。"""
        board = self.board_repository.get_by_id(db, board_id)
        if board is None:
            raise NotFoundError(error_messages.BOARD_NOT_FOUND)
        return board

    def update_board(
        self,
        db: Session,
        *,
        board_id: int,
        board_in: BoardUpdate,
        actor_id: int | None,
    ) -> Board:
        """ボードを更新する。"""
        board = self.get_board(db, board_id)
        raise_if_version_conflict(
            current_version=board.version,
            requested_version=board_in.version,
            current=BoardRead.model_validate(board).model_dump(),
        )
        board = self.board_repository.update(
            db,
            board=board,
            board_in=board_in,
            actor_id=actor_id,
        )
        self._record_change(
            db,
            project_id=board.project_id,
            target_type=TaskTargetType.BOARD,
            target_id=board.id,
            action=TaskChangeLogAction.BOARD_UPDATED,
            changed_by=actor_id,
        )
        return board

    def delete_board(self, db: Session, *, board_id: int, actor_id: int | None) -> None:
        """ボードを論理削除する。"""
        board = self.get_board(db, board_id)
        self.board_repository.soft_delete(db, board=board, actor_id=actor_id)
        self._record_change(
            db,
            project_id=board.project_id,
            target_type=TaskTargetType.BOARD,
            target_id=board.id,
            action=TaskChangeLogAction.BOARD_DELETED,
            changed_by=actor_id,
        )

    def create_board_column(
        self,
        db: Session,
        *,
        board_id: int,
        column_in: BoardColumnCreate,
        actor_id: int | None,
    ) -> BoardColumn:
        """ボード列を作成する。"""
        board = self.get_board(db, board_id)
        try:
            column = self.column_repository.create(
                db,
                board_id=board_id,
                column_in=column_in,
            )
        except IntegrityError as exc:
            raise_duplicate_after_rollback(
                db,
                error_messages.DUPLICATE_RESOURCE,
                exc,
            )
        self._record_change(
            db,
            project_id=board.project_id,
            target_type=TaskTargetType.COLUMN,
            target_id=column.id,
            action=TaskChangeLogAction.COLUMN_CREATED,
            changed_by=actor_id,
        )
        return column

    def list_board_columns(self, db: Session, board_id: int) -> list[BoardColumn]:
        """ボード列一覧を取得する。"""
        self.get_board(db, board_id)
        return self.column_repository.list_by_board(db, board_id)

    def get_board_column(self, db: Session, column_id: int) -> BoardColumn:
        """ボード列を取得する。"""
        column = self.column_repository.get_by_id(db, column_id)
        if column is None:
            raise NotFoundError(error_messages.BOARD_COLUMN_NOT_FOUND)
        return column

    def update_board_column(
        self,
        db: Session,
        *,
        column_id: int,
        column_in: BoardColumnUpdate,
        actor_id: int | None,
    ) -> BoardColumn:
        """ボード列を更新する。"""
        column = self.get_board_column(db, column_id)
        board = self.get_board(db, column.board_id)
        raise_if_version_conflict(
            current_version=column.version,
            requested_version=column_in.version,
            current=BoardColumnRead.model_validate(column).model_dump(),
        )
        try:
            column = self.column_repository.update(
                db,
                column=column,
                column_in=column_in,
            )
        except IntegrityError as exc:
            raise_duplicate_after_rollback(
                db,
                error_messages.DUPLICATE_RESOURCE,
                exc,
            )
        self._record_change(
            db,
            project_id=board.project_id,
            target_type=TaskTargetType.COLUMN,
            target_id=column.id,
            action=TaskChangeLogAction.COLUMN_UPDATED,
            changed_by=actor_id,
        )
        return column

    def delete_board_column(
        self,
        db: Session,
        *,
        column_id: int,
        actor_id: int | None,
    ) -> None:
        """ボード列を論理削除する。"""
        column = self.get_board_column(db, column_id)
        board = self.get_board(db, column.board_id)
        self.column_repository.soft_delete(db, column)
        self._record_change(
            db,
            project_id=board.project_id,
            target_type=TaskTargetType.COLUMN,
            target_id=column.id,
            action=TaskChangeLogAction.COLUMN_DELETED,
            changed_by=actor_id,
        )

    def get_gantt(
        self,
        db: Session,
        *,
        project_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
        requirement_id: int | None = None,
        assignee_id: int | None = None,
    ) -> tuple[list[Task], list[TaskDependency], list[Milestone]]:
        """ガントチャート用データを取得する。"""
        self._ensure_project_exists(db, project_id)
        tasks = self.task_repository.list_for_gantt(
            db,
            project_id=project_id,
            start_date=start_date,
            end_date=end_date,
            requirement_id=requirement_id,
            assignee_id=assignee_id,
        )
        dependencies = self.dependency_repository.list_by_project(db, project_id)
        milestones = self.milestone_repository.list_by_project(db, project_id)
        return tasks, dependencies, milestones

    def ensure_user_can_access_task(
        self,
        db: Session,
        *,
        user: User,
        task: Task,
        permission_code: str,
    ) -> None:
        """ユーザーがタスク操作権限を持つことを確認する。"""
        if not self.authorization_service.has_project_permission(
            db,
            user=user,
            project_id=task.project_id,
            permission_code=permission_code,
        ):
            raise ForbiddenError()

    def ensure_user_can_access_project_resource(
        self,
        db: Session,
        *,
        user: User,
        project_id: int,
        permission_code: str,
    ) -> None:
        """ユーザーがプロジェクト配下リソースの操作権限を持つことを確認する。"""
        if not self.authorization_service.has_project_permission(
            db,
            user=user,
            project_id=project_id,
            permission_code=permission_code,
        ):
            raise ForbiddenError()

    def build_task_read(self, db: Session, task: Task) -> TaskRead:
        """タスクモデルからレスポンスschemaを作成する。"""
        return self.build_task_reads(db, [task])[0]

    def build_task_reads(self, db: Session, tasks: list[Task]) -> list[TaskRead]:
        """複数タスクモデルからレスポンスschemaを作成する。

        Args:
            db: DBセッション。
            tasks: タスクモデル一覧。

        Returns:
            タスクレスポンス一覧。
        """
        summaries_by_task_id = (
            self.relation_repository.list_requirement_summaries_by_task_ids(
                db,
                [task.id for task in tasks],
            )
        )
        return [
            self._build_task_read(
                db,
                task,
                requirements=summaries_by_task_id.get(task.id, []),
            )
            for task in tasks
        ]

    def _build_task_read(
        self,
        db: Session,
        task: Task,
        *,
        requirements: list[dict[str, Any]],
    ) -> TaskRead:
        """関連要件を含むタスクレスポンスschemaを作成する。"""
        task_read = TaskRead.model_validate(task)
        task_read.is_overdue = self.is_overdue(task)
        task_read.is_blocked = self.is_blocked(db, task)
        task_read.requirements = [
            TaskRequirementSummary.model_validate(requirement)
            for requirement in requirements
        ]
        return task_read

    def is_overdue(self, task: Task) -> bool:
        """タスクが期限超過しているか判定する。"""
        return (
            task.due_date is not None
            and task.due_date < date.today()
            and task.status not in {"done", "cancelled"}
        )

    def is_blocked(self, db: Session, task: Task) -> bool:
        """タスクが依存関係によりブロックされているか判定する。"""
        dependencies = self.dependency_repository.list_by_task(db, task.id)
        for dependency in dependencies:
            if (
                dependency.successor_task_id == task.id
                and dependency.dependency_type == "finish_to_start"
            ):
                predecessor = self.task_repository.get_by_id(
                    db,
                    dependency.predecessor_task_id,
                )
                if predecessor is not None and predecessor.status != "done":
                    return True
        return False

    def _ensure_project_exists(self, db: Session, project_id: int) -> None:
        """プロジェクトが存在することを確認する。"""
        if self.project_repository.get_by_id(db, project_id) is None:
            raise NotFoundError(error_messages.PROJECT_NOT_FOUND)

    def _ensure_requirement_in_project(
        self,
        db: Session,
        project_id: int,
        requirement_id: int,
    ) -> None:
        """要件がプロジェクトに属することを確認する。"""
        requirement_project_id = self._get_requirement_project_id(db, requirement_id)
        if requirement_project_id != project_id:
            raise NotFoundError(error_messages.REQUIREMENT_NOT_FOUND)

    def _get_requirement_project_id(self, db: Session, requirement_id: int) -> int:
        """要件が属するプロジェクトIDを取得する。"""
        project_id = self.requirement_lookup_repository.get_requirement_project_id(
            db,
            requirement_id,
        )
        if project_id is None:
            raise NotFoundError(error_messages.REQUIREMENT_NOT_FOUND)
        return project_id

    def _validate_parent_task(
        self,
        db: Session,
        project_id: int,
        parent_task_id: int | None,
    ) -> None:
        """親タスクが同じプロジェクトに存在することを確認する。"""
        if parent_task_id is None:
            return
        parent = self.task_repository.get_by_id(db, parent_task_id)
        if parent is None or parent.project_id != project_id:
            raise NotFoundError(error_messages.TASK_PARENT_NOT_FOUND)

    def _normalize_task_code(self, task_code: str | None) -> str | None:
        """タスクコード入力値を正規化する。

        Args:
            task_code: リクエストで受け取ったタスクコード。

        Returns:
            空文字を未指定扱いにしたタスクコード。
        """
        if task_code is None:
            return None
        stripped = task_code.strip()
        return stripped or None

    def _generate_task_code(self, db: Session, project_id: int) -> str:
        """プロジェクト単位のタスクコードを採番する。

        Args:
            db: DBセッション。
            project_id: 採番対象プロジェクトID。

        Returns:
            `TASK-001` 形式のタスクコード。
        """
        next_number = self.task_repository.get_max_auto_task_number(db, project_id) + 1
        return f"{AUTO_TASK_CODE_PREFIX}-{next_number:03d}"

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
        task = self.get_task(db, comment.task_id)
        raise_if_version_conflict(
            current_version=comment.version,
            requested_version=comment_in.version,
            current=TaskCommentRead.model_validate(comment).model_dump(),
        )
        old_value = comment.is_resolved
        comment = self.comment_repository.set_resolved(
            db,
            comment=comment,
            is_resolved=is_resolved,
            actor_id=actor_id,
        )
        self._record_change(
            db,
            project_id=task.project_id,
            target_type=TaskTargetType.COMMENT,
            target_id=comment.id,
            action=(
                TaskChangeLogAction.COMMENT_RESOLVED
                if is_resolved
                else TaskChangeLogAction.COMMENT_REOPENED
            ),
            field_name="is_resolved",
            old_value={"task_id": task.id, "is_resolved": old_value},
            new_value={"task_id": task.id, "is_resolved": comment.is_resolved},
            changed_by=actor_id,
        )
        return comment

    def _validate_task_state(self, status: str, progress_percent: int) -> None:
        """タスク状態と進捗率の整合性を検証する。"""
        if status == "done" and progress_percent != 100:
            raise BadRequestError(error_messages.TASK_DONE_PROGRESS_INVALID)

    def _normalize_task_dates(
        self,
        *,
        status: str,
        progress_percent: int,
        actual_start_date: date | None,
        actual_end_date: date | None,
    ) -> dict[str, object]:
        """状態変更に応じて進捗率と実績日を補完する。"""
        today = date.today()
        normalized: dict[str, object] = {
            "progress_percent": progress_percent,
            "actual_start_date": actual_start_date,
            "actual_end_date": actual_end_date,
        }
        if status == "in_progress" and actual_start_date is None:
            normalized["actual_start_date"] = today
        if status == "done":
            normalized["progress_percent"] = 100
            if actual_end_date is None:
                normalized["actual_end_date"] = today
        return normalized

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

    def _task_snapshot(self, task: Task) -> dict[str, Any]:
        """タスクの変更前スナップショットを作成する。"""
        return {
            "parent_task_id": task.parent_task_id,
            "task_code": task.task_code,
            "title": task.title,
            "description": task.description,
            "task_type": task.task_type,
            "status": task.status,
            "priority": task.priority,
            "assignee_id": task.assignee_id,
            "reporter_id": task.reporter_id,
            "start_date": task.start_date.isoformat() if task.start_date else None,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "actual_start_date": (
                task.actual_start_date.isoformat() if task.actual_start_date else None
            ),
            "actual_end_date": (
                task.actual_end_date.isoformat() if task.actual_end_date else None
            ),
            "progress_percent": task.progress_percent,
            "estimated_minutes": task.estimated_minutes,
            "actual_minutes": task.actual_minutes,
            "sort_order": task.sort_order,
            "tags": task.tags,
        }

    def _build_task_change_log_read(
        self,
        change_log: TaskChangeLog,
        *,
        users_by_id: dict[int, ChangeLogUserRead],
        tasks_by_id: dict[int, Task],
        requirements_by_id: dict[int, Requirement],
    ) -> TaskChangeLogRead:
        """タスク変更履歴レスポンスを作成する。"""
        field_name = TASK_CHANGE_LOG_FORMATTER.normalize_field_name(
            change_log.field_name,
        )
        value_formatters = {
            "parent_task_id": lambda value: self._format_task_id_label_value(
                value,
                tasks_by_id,
            ),
            "requirements": lambda value: self._format_requirement_values(
                value,
                requirements_by_id,
            ),
        }
        return TaskChangeLogRead(
            id=change_log.id,
            task_id=(
                change_log.target_id
                if change_log.target_type == TaskTargetType.TASK
                else self._get_task_id_from_comment_log(change_log)
            ),
            target_type=self._normalize_change_log_target_type(
                change_log.target_type,
            ),
            action=self._normalize_change_log_action(change_log.action),
            field_name=field_name,
            old_value=TASK_CHANGE_LOG_FORMATTER.extract_change_value(
                change_log.old_value,
                field_name,
                users_by_id=users_by_id,
                value_formatters=value_formatters,
            ),
            new_value=TASK_CHANGE_LOG_FORMATTER.extract_change_value(
                change_log.new_value,
                field_name,
                users_by_id=users_by_id,
                value_formatters=value_formatters,
            ),
            reason=change_log.reason,
            created_by=change_log.changed_by,
            created_by_user=(
                users_by_id.get(change_log.changed_by)
                if change_log.changed_by is not None
                else None
            ),
            created_at=change_log.changed_at,
        )

    def _get_task_id_from_comment_log(self, change_log: TaskChangeLog) -> int:
        """コメント履歴のレスポンス用タスクIDを取得する。

        Args:
            change_log: タスク変更履歴モデル。

        Returns:
            コメントに紐づくタスクID。取得できない場合はtarget_id。
        """
        task_id = None
        if change_log.new_value is not None:
            task_id = change_log.new_value.get("task_id")
        if task_id is None and change_log.old_value is not None:
            task_id = change_log.old_value.get("task_id")
        return task_id if isinstance(task_id, int) else change_log.target_id

    def _normalize_change_log_action(self, action: str) -> str:
        """DB保存済みの操作種別をAPI用の安定コードに変換する。"""
        return TASK_CHANGE_LOG_FORMATTER.normalize_action(action)

    def _normalize_change_log_target_type(self, target_type: str) -> str:
        """DB保存済みの対象種別をAPI用の安定コードに変換する。"""
        return TASK_CHANGE_LOG_FORMATTER.normalize_target_type(target_type)

    def _get_change_log_users_by_id(
        self,
        db: Session,
        user_ids: list[int | None],
    ) -> dict[int, ChangeLogUserRead]:
        """変更履歴に含まれるユーザー概要を取得する。"""
        ids = sorted({user_id for user_id in user_ids if user_id is not None})
        users = self.user_repository.list_by_ids(db, ids)
        return {
            user.id: ChangeLogUserRead(
                id=user.id,
                name=user.name,
                email=user.email,
                avatar_url=None,
            )
            for user in users
        }

    def _get_change_log_tasks_by_id(
        self,
        db: Session,
        task_ids: list[int],
    ) -> dict[int, Task]:
        """変更履歴に含まれるタスク表示補助情報を取得する。

        Args:
            db: DBセッション。
            task_ids: 取得対象のタスクID一覧。

        Returns:
            タスクIDをキーにしたタスク辞書。
        """
        ids = sorted(set(task_ids))
        return {task.id: task for task in self.task_repository.list_by_ids(db, ids)}

    def _get_change_log_requirements_by_id(
        self,
        db: Session,
        requirement_ids: list[int],
    ) -> dict[int, Requirement]:
        """変更履歴に含まれる要件表示補助情報を取得する。

        Args:
            db: DBセッション。
            requirement_ids: 取得対象の要件ID一覧。

        Returns:
            要件IDをキーにした要件辞書。
        """
        ids = sorted(set(requirement_ids))
        requirements = self.requirement_lookup_repository.list_requirements_by_ids(
            db,
            ids,
        )
        return {requirement.id: requirement for requirement in requirements}

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

    def _collect_change_log_task_ids(
        self,
        change_logs: list[TaskChangeLog],
    ) -> list[int]:
        """変更履歴レスポンス整形に必要なタスクIDを集める。

        Args:
            change_logs: 整形対象の変更履歴一覧。

        Returns:
            表示補助に必要なタスクID一覧。
        """
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
        """変更履歴レスポンス整形に必要な要件IDを集める。

        Args:
            change_logs: 整形対象の変更履歴一覧。

        Returns:
            表示補助に必要な要件ID一覧。
        """
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
        """変更履歴値から要件IDを抽出する。

        Args:
            value: 変更履歴に保存されている要件関連値。

        Returns:
            抽出できた要件ID一覧。
        """
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

    def _format_task_id_label_value(
        self,
        value: Any,
        tasks_by_id: dict[int, Task],
    ) -> dict[str, Any] | None:
        """タスクIDをid/label形式に変換する。

        Args:
            value: 変更履歴に保存されているタスクID。
            tasks_by_id: 表示補助に使うタスク辞書。

        Returns:
            id/label形式の値。値がNoneの場合はNone。
        """
        if value is None:
            return None
        task = tasks_by_id.get(value) if isinstance(value, int) else None
        label = f"{task.task_code} {task.title}" if task is not None else str(value)
        return {"id": value, "label": label}

    def _format_requirement_values(
        self,
        value: Any,
        requirements_by_id: dict[int, Requirement],
    ) -> Any:
        """要件IDを表示補助付きの配列に変換する。

        Args:
            value: 変更履歴に保存されている要件関連値。
            requirements_by_id: 表示補助に使う要件辞書。

        Returns:
            要件表示補助値。配列以外の値は単一値として変換する。
        """
        if value is None:
            return None
        if isinstance(value, list):
            return [
                self._format_requirement_value(item, requirements_by_id)
                for item in value
            ]
        return self._format_requirement_value(value, requirements_by_id)

    def _format_requirement_value(
        self,
        value: Any,
        requirements_by_id: dict[int, Requirement],
    ) -> dict[str, Any] | Any:
        """単一の要件値を表示補助付きに変換する。

        Args:
            value: 変更履歴に保存されている単一要件値。
            requirements_by_id: 表示補助に使う要件辞書。

        Returns:
            id/requirement_code/label形式の値。IDが取れない場合は元の値。
        """
        requirement_ids = self._extract_requirement_ids(value)
        if not requirement_ids:
            return value
        requirement_id = requirement_ids[0]
        requirement = requirements_by_id.get(requirement_id)
        if requirement is None:
            return {
                "id": requirement_id,
                "requirement_code": str(requirement_id),
                "label": str(requirement_id),
            }
        label = f"{requirement.requirement_code} {requirement.title}"
        return {
            "id": requirement.id,
            "requirement_code": requirement.requirement_code,
            "label": label,
        }

    def _record_task_update_logs(
        self,
        db: Session,
        *,
        task: Task,
        old_values: dict[str, Any],
        new_values: dict[str, Any],
        updated_fields: list[str],
        reason: str | None,
        actor_id: int | None,
    ) -> None:
        """タスク更新の変更履歴を記録する。"""
        self._record_change(
            db,
            project_id=task.project_id,
            target_type=TaskTargetType.TASK,
            target_id=task.id,
            action=TaskChangeLogAction.UPDATED,
            field_name=None,
            old_value={"snapshot": old_values},
            new_value={
                "updated_fields": updated_fields,
                "snapshot": new_values,
            },
            reason=reason,
            changed_by=actor_id,
        )
        field_action_map = {
            "status": TaskChangeLogAction.STATUS_CHANGED,
            "assignee_id": TaskChangeLogAction.ASSIGNEE_CHANGED,
            "start_date": TaskChangeLogAction.SCHEDULE_CHANGED,
            "due_date": TaskChangeLogAction.SCHEDULE_CHANGED,
            "progress_percent": TaskChangeLogAction.PROGRESS_CHANGED,
        }
        for field_name, action in field_action_map.items():
            if field_name in updated_fields:
                self._record_change(
                    db,
                    project_id=task.project_id,
                    target_type=TaskTargetType.TASK,
                    target_id=task.id,
                    action=action,
                    field_name=field_name,
                    old_value={field_name: old_values.get(field_name)},
                    new_value={field_name: new_values.get(field_name)},
                    reason=reason,
                    changed_by=actor_id,
                )

    def _record_change(
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
    ) -> None:
        """タスク変更履歴を記録する。"""
        self.change_log_repository.create(
            db,
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

    def _record_audit(
        self,
        db: Session,
        *,
        event_type: str,
        actor_id: int | None,
        project_id: int,
        resource_id: int,
        metadata: dict[str, Any],
    ) -> None:
        """タスク操作の監査ログを記録する。"""
        self.audit_log_service.record(
            db,
            event_type=event_type,
            actor_user_id=actor_id,
            project_id=project_id,
            resource_type="task",
            resource_id=resource_id,
            metadata=metadata,
        )
