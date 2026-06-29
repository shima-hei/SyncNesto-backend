"""タスク管理のビジネスロジックを提供するモジュール。"""

from datetime import date
from typing import Any, TypedDict

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import (
    BadRequestError,
    DuplicateResourceError,
    ForbiddenError,
    NotFoundError,
)
from app.models.task import (
    Milestone,
    RequirementTaskRelation,
    Task,
    TaskDependency,
)
from app.models.user import User
from app.repositories.project import ProjectRepository
from app.repositories.task_change_log import TaskChangeLogRepository
from app.repositories.task_comment import TaskCommentRepository
from app.repositories.task_dependency import TaskDependencyRepository
from app.repositories.task_item import TaskRepository
from app.repositories.task_milestone import MilestoneRepository
from app.repositories.task_requirement import (
    RequirementTaskRelationRepository,
    TaskRequirementLookupRepository,
)
from app.schemas.task import (
    RequirementTaskProgressRead,
    TaskCreate,
    TaskUpdate,
)
from app.services.audit_log import AuditLogService
from app.services.authorization import AuthorizationService
from app.services.change_log_formatter import (
    ChangeLogFormatConfig,
    ChangeLogFormatter,
    build_changed_field_snapshots,
    build_update_change_log_entry,
)
from app.services.conflict import (
    raise_duplicate_after_rollback,
    raise_if_version_conflict,
)
from app.services.task_presentation import (
    TaskPresentationData,
    TaskPresentationDataService,
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


class TaskDateNormalization(TypedDict):
    """状態変更時に補完したタスク日付・進捗値。"""

    progress_percent: int
    actual_start_date: date | None
    actual_end_date: date | None


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
        requirement_lookup_repository: TaskRequirementLookupRepository | None = None,
        project_repository: ProjectRepository | None = None,
        authorization_service: AuthorizationService | None = None,
        change_log_repository: TaskChangeLogRepository | None = None,
        comment_repository: TaskCommentRepository | None = None,
        audit_log_service: AuditLogService | None = None,
        presentation_data_service: TaskPresentationDataService | None = None,
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
        self.requirement_lookup_repository = (
            requirement_lookup_repository or TaskRequirementLookupRepository()
        )
        self.project_repository = project_repository or ProjectRepository()
        self.authorization_service = authorization_service or AuthorizationService()
        self.change_log_repository = change_log_repository or TaskChangeLogRepository()
        self.comment_repository = comment_repository or TaskCommentRepository()
        self.audit_log_service = audit_log_service or AuditLogService()
        self.presentation_data_service = (
            presentation_data_service or TaskPresentationDataService(
                task_repository=self.task_repository,
                relation_repository=self.relation_repository,
                dependency_repository=self.dependency_repository,
            )
        )

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
        """プロジェクト内タスク一覧を取得する。"""
        self._ensure_project_exists(db, project_id)
        if requirement_id is not None:
            self._ensure_requirement_in_project(db, project_id, requirement_id)
        if parent_task_id is not None and root_only:
            raise BadRequestError(error_messages.TASK_PARENT_FILTER_CONFLICT)
        if parent_task_id is not None:
            self._validate_parent_task(db, project_id, parent_task_id)
        return self.task_repository.list_paginated(
            db,
            project_id=project_id,
            page=page,
            page_size=page_size,
            status=status,
            assignee_id=assignee_id,
            requirement_id=requirement_id,
            parent_task_id=parent_task_id,
            root_only=root_only,
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

    def list_task_tags(self, db: Session, project_id: int) -> list[str]:
        """プロジェクト内で利用済みのタスクタグ候補を取得する。"""
        self._ensure_project_exists(db, project_id)
        return self.task_repository.list_tags(db, project_id)

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
        current = self._build_task_conflict_current(db, task)
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
            self._validate_parent_task(
                db,
                task.project_id,
                task_in.parent_task_id,
                task_id=task.id,
            )
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

        candidate_fields = (
            task_in.model_fields_set - {"version", "change_reason"}
        ) & TASK_UPDATABLE_FIELDS
        updated_fields, old_values, new_values = build_changed_field_snapshots(
            before_snapshot,
            self._task_snapshot(task),
            candidate_fields,
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
        return self.presentation_data_service.get_task_presentation_data(
            db,
            tasks,
        )

    def _build_task_conflict_current(
        self,
        db: Session,
        task: Task,
    ) -> dict[str, Any]:
        """排他制御エラーで返す現行タスク値を組み立てる。

        Args:
            db: DBセッション。
            task: 現行タスクモデル。

        Returns:
            TaskReadと同じキーを持つ現行タスク値。
        """
        presentation_data = self.get_task_presentation_data(db, [task])
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
            "start_date": task.start_date,
            "due_date": task.due_date,
            "actual_start_date": task.actual_start_date,
            "actual_end_date": task.actual_end_date,
            "progress_percent": task.progress_percent,
            "estimated_minutes": task.estimated_minutes,
            "actual_minutes": task.actual_minutes,
            "sort_order": task.sort_order,
            "tags": task.tags,
            "id": task.id,
            "project_id": task.project_id,
            "version": task.version,
            "created_by": task.created_by,
            "updated_by": task.updated_by,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "is_overdue": self.is_overdue(task),
            "is_blocked": task.id in presentation_data.blocked_task_ids,
            "requirements": presentation_data.requirements_by_task_id.get(
                task.id,
                [],
            ),
        }

    def is_overdue(self, task: Task) -> bool:
        """タスクが期限超過しているか判定する。"""
        return (
            task.due_date is not None
            and task.due_date < date.today()
            and task.status not in {"done", "cancelled"}
        )

    def is_blocked(self, db: Session, task: Task) -> bool:
        """タスクが依存関係によりブロックされているか判定する。"""
        return self.presentation_data_service.is_blocked(db, task)

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
        task_id: int | None = None,
    ) -> None:
        """親タスクが同じプロジェクトに存在することを確認する。"""
        if parent_task_id is None:
            return
        if task_id is not None and parent_task_id == task_id:
            raise BadRequestError(error_messages.TASK_PARENT_SELF_REFERENCE)
        parent = self.task_repository.get_by_id(db, parent_task_id)
        if parent is None or parent.project_id != project_id:
            raise NotFoundError(error_messages.TASK_PARENT_NOT_FOUND)
        if task_id is not None and self._creates_parent_cycle(
            db,
            task_id=task_id,
            parent_task_id=parent_task_id,
        ):
            raise BadRequestError(error_messages.TASK_PARENT_CYCLE)

    def _creates_parent_cycle(
        self,
        db: Session,
        *,
        task_id: int,
        parent_task_id: int,
    ) -> bool:
        """親タスク変更により循環参照が発生するか判定する。

        Args:
            db: DBセッション。
            task_id: 更新対象タスクID。
            parent_task_id: 新しい親タスクID。

        Returns:
            循環参照が発生する場合はTrue。
        """
        current_parent_id: int | None = parent_task_id
        visited_task_ids: set[int] = set()
        while current_parent_id is not None:
            if current_parent_id == task_id:
                return True
            if current_parent_id in visited_task_ids:
                return True
            visited_task_ids.add(current_parent_id)
            parent = self.task_repository.get_by_id(db, current_parent_id)
            current_parent_id = parent.parent_task_id if parent is not None else None
        return False

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
    ) -> TaskDateNormalization:
        """状態変更に応じて進捗率と実績日を補完する。"""
        today = date.today()
        normalized: TaskDateNormalization = {
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
        change_log_entry = build_update_change_log_entry(
            updated_fields=updated_fields,
            old_values=old_values,
            new_values=new_values,
            default_action=TaskChangeLogAction.UPDATED,
            field_action_map={
                "status": TaskChangeLogAction.STATUS_CHANGED,
                "assignee_id": TaskChangeLogAction.ASSIGNEE_CHANGED,
                "start_date": TaskChangeLogAction.SCHEDULE_CHANGED,
                "due_date": TaskChangeLogAction.SCHEDULE_CHANGED,
                "actual_start_date": TaskChangeLogAction.SCHEDULE_CHANGED,
                "actual_end_date": TaskChangeLogAction.SCHEDULE_CHANGED,
                "progress_percent": TaskChangeLogAction.PROGRESS_CHANGED,
            },
        )
        if change_log_entry is None:
            return

        self._record_change(
            db,
            project_id=task.project_id,
            target_type=TaskTargetType.TASK,
            target_id=task.id,
            action=change_log_entry.action,
            field_name=change_log_entry.field_name,
            old_value=change_log_entry.old_value,
            new_value=change_log_entry.new_value,
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
