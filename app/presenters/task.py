"""タスク管理関連レスポンスのPresenterを定義するモジュール。"""

from datetime import date
from typing import Any, cast

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
from app.schemas.change_log import (
    ChangeLogUserRead,
    ChangeLogValue,
    TaskChangeLogActionCode,
    TaskChangeLogFieldName,
    TaskChangeLogTargetTypeCode,
)
from app.schemas.task import (
    BoardColumnRead,
    BoardRead,
    GanttResponse,
    MilestoneRead,
    RequirementTaskRelationRead,
    TaskChangeLogListResponse,
    TaskChangeLogRead,
    TaskCommentRead,
    TaskDependencyRead,
    TaskListResponse,
    TaskRead,
    TaskRequirementSummary,
    TaskTagListResponse,
)
from app.services.task import (
    TASK_CHANGE_LOG_FORMATTER,
    TaskChangeLogAction,
    TaskTargetType,
)
from app.services.task_change_log import TaskChangeLogPresentationData
from app.services.task_presentation import TaskPresentationData


class TaskPresenter:
    """タスク管理APIのレスポンス変換を担当する。"""

    def build_task_response(
        self,
        task: Task,
        *,
        presentation_data: TaskPresentationData,
    ) -> TaskRead:
        """タスクレスポンスを組み立てる。

        Args:
            task: タスクモデル。
            presentation_data: 表示補助に使う関連モデル一式。

        Returns:
            タスクレスポンス。
        """
        return self._build_task_response(
            task,
            presentation_data=presentation_data,
        )

    def build_task_responses(
        self,
        tasks: list[Task],
        *,
        presentation_data: TaskPresentationData,
    ) -> list[TaskRead]:
        """タスク一覧レスポンスを組み立てる。

        Args:
            tasks: タスクモデル一覧。
            presentation_data: 表示補助に使う関連モデル一式。

        Returns:
            タスクレスポンス一覧。
        """
        return [
            self._build_task_response(
                task,
                presentation_data=presentation_data,
            )
            for task in tasks
        ]

    def build_task_list_response(
        self,
        *,
        tasks: list[Task],
        total: int,
        page: int,
        page_size: int,
        presentation_data: TaskPresentationData,
    ) -> TaskListResponse:
        """タスク一覧レスポンスを組み立てる。

        Args:
            tasks: タスクモデル一覧。
            total: 全件数。
            page: ページ番号。
            page_size: 1ページあたりの件数。
            presentation_data: 表示補助に使う関連モデル一式。

        Returns:
            タスク一覧レスポンス。
        """
        return TaskListResponse(
            items=self.build_task_responses(
                tasks,
                presentation_data=presentation_data,
            ),
            total=total,
            page=page,
            page_size=page_size,
        )

    def build_task_tag_list_response(self, tags: list[str]) -> TaskTagListResponse:
        """タスクタグ候補一覧レスポンスを組み立てる。

        Args:
            tags: タグ候補一覧。

        Returns:
            タスクタグ候補一覧レスポンス。
        """
        return TaskTagListResponse(items=tags)

    def build_requirement_task_relation_response(
        self,
        relation: RequirementTaskRelation,
    ) -> RequirementTaskRelationRead:
        """要件タスク関連レスポンスを組み立てる。

        Args:
            relation: 要件タスク関連モデル。

        Returns:
            要件タスク関連レスポンス。
        """
        return RequirementTaskRelationRead.model_validate(relation)

    def build_task_comment_response(
        self,
        comment: TaskComment,
        *,
        user: User | None = None,
    ) -> TaskCommentRead:
        """タスクコメントレスポンスを組み立てる。

        Args:
            comment: タスクコメントモデル。
            user: コメント作成者。

        Returns:
            タスクコメントレスポンス。
        """
        return TaskCommentRead.model_validate(comment).model_copy(
            update={
                "created_by_user": (
                    self._build_change_log_user(user)
                    if user is not None
                    else None
                ),
            },
        )

    def build_task_comment_responses(
        self,
        comments: list[TaskComment],
        *,
        users_by_id: dict[int, User],
    ) -> list[TaskCommentRead]:
        """タスクコメントレスポンス一覧を組み立てる。

        Args:
            comments: タスクコメントモデル一覧。
            users_by_id: コメント作成者IDをkeyにしたユーザー辞書。

        Returns:
            タスクコメントレスポンス一覧。
        """
        return [
            self.build_task_comment_response(
                comment,
                user=(
                    users_by_id.get(comment.created_by)
                    if comment.created_by is not None
                    else None
                ),
            )
            for comment in comments
        ]

    def build_task_change_log_list_response(
        self,
        *,
        change_logs: list[TaskChangeLog],
        total: int,
        page: int,
        page_size: int,
        presentation_data: TaskChangeLogPresentationData,
    ) -> TaskChangeLogListResponse:
        """タスク変更履歴一覧レスポンスを組み立てる。

        Args:
            change_logs: タスク変更履歴モデル一覧。
            total: 全件数。
            page: ページ番号。
            page_size: 1ページあたりの件数。
            presentation_data: 表示補助に使う関連モデル一式。

        Returns:
            タスク変更履歴一覧レスポンス。
        """
        return TaskChangeLogListResponse(
            items=self.build_task_change_log_responses(
                change_logs,
                presentation_data,
            ),
            total=total,
            page=page,
            page_size=page_size,
        )

    def build_task_change_log_responses(
        self,
        change_logs: list[TaskChangeLog],
        presentation_data: TaskChangeLogPresentationData,
    ) -> list[TaskChangeLogRead]:
        """タスク変更履歴レスポンス一覧を組み立てる。

        Args:
            change_logs: タスク変更履歴モデル一覧。
            presentation_data: 表示補助に使う関連モデル一式。

        Returns:
            タスク変更履歴レスポンス一覧。
        """
        users_by_id = {
            user_id: self._build_change_log_user(user)
            for user_id, user in presentation_data.users_by_id.items()
        }
        return [
            self._build_task_change_log_response(
                change_log,
                users_by_id=users_by_id,
                tasks_by_id=presentation_data.tasks_by_id,
                requirements_by_id=presentation_data.requirements_by_id,
                comments_by_id=presentation_data.comments_by_id,
            )
            for change_log in change_logs
        ]

    def build_task_dependency_response(
        self,
        dependency: TaskDependency,
    ) -> TaskDependencyRead:
        """タスク依存関係レスポンスを組み立てる。

        Args:
            dependency: タスク依存関係モデル。

        Returns:
            タスク依存関係レスポンス。
        """
        return TaskDependencyRead.model_validate(dependency)

    def build_task_dependency_responses(
        self,
        dependencies: list[TaskDependency],
    ) -> list[TaskDependencyRead]:
        """タスク依存関係一覧レスポンスを組み立てる。

        Args:
            dependencies: タスク依存関係モデル一覧。

        Returns:
            タスク依存関係レスポンス一覧。
        """
        return [
            self.build_task_dependency_response(dependency)
            for dependency in dependencies
        ]

    def build_board_response(self, board: Board) -> BoardRead:
        """ボードレスポンスを組み立てる。

        Args:
            board: ボードモデル。

        Returns:
            ボードレスポンス。
        """
        return BoardRead.model_validate(board)

    def build_board_responses(self, boards: list[Board]) -> list[BoardRead]:
        """ボード一覧レスポンスを組み立てる。

        Args:
            boards: ボードモデル一覧。

        Returns:
            ボードレスポンス一覧。
        """
        return [self.build_board_response(board) for board in boards]

    def build_board_column_response(
        self,
        column: BoardColumn,
    ) -> BoardColumnRead:
        """ボード列レスポンスを組み立てる。

        Args:
            column: ボード列モデル。

        Returns:
            ボード列レスポンス。
        """
        return BoardColumnRead.model_validate(column)

    def build_board_column_responses(
        self,
        columns: list[BoardColumn],
    ) -> list[BoardColumnRead]:
        """ボード列一覧レスポンスを組み立てる。

        Args:
            columns: ボード列モデル一覧。

        Returns:
            ボード列レスポンス一覧。
        """
        return [self.build_board_column_response(column) for column in columns]

    def build_milestone_response(self, milestone: Milestone) -> MilestoneRead:
        """マイルストーンレスポンスを組み立てる。

        Args:
            milestone: マイルストーンモデル。

        Returns:
            マイルストーンレスポンス。
        """
        return MilestoneRead.model_validate(milestone)

    def build_milestone_responses(
        self,
        milestones: list[Milestone],
    ) -> list[MilestoneRead]:
        """マイルストーン一覧レスポンスを組み立てる。

        Args:
            milestones: マイルストーンモデル一覧。

        Returns:
            マイルストーンレスポンス一覧。
        """
        return [
            self.build_milestone_response(milestone)
            for milestone in milestones
        ]

    def build_gantt_response(
        self,
        *,
        tasks: list[Task],
        dependencies: list[TaskDependency],
        milestones: list[Milestone],
        presentation_data: TaskPresentationData,
    ) -> GanttResponse:
        """ガントチャートレスポンスを組み立てる。

        Args:
            tasks: タスクモデル一覧。
            dependencies: タスク依存関係モデル一覧。
            milestones: マイルストーンモデル一覧。
            presentation_data: 表示補助に使う関連モデル一式。

        Returns:
            ガントチャートレスポンス。
        """
        return GanttResponse(
            tasks=self.build_task_responses(
                tasks,
                presentation_data=presentation_data,
            ),
            dependencies=self.build_task_dependency_responses(dependencies),
            milestones=self.build_milestone_responses(milestones),
        )

    def _build_task_change_log_response(
        self,
        change_log: TaskChangeLog,
        *,
        users_by_id: dict[int, ChangeLogUserRead],
        tasks_by_id: dict[int, Task],
        requirements_by_id: dict[int, Requirement],
        comments_by_id: dict[int, TaskComment],
    ) -> TaskChangeLogRead:
        """タスク変更履歴レスポンスを組み立てる。"""
        field_name = cast(
            TaskChangeLogFieldName | None,
            TASK_CHANGE_LOG_FORMATTER.normalize_field_name(
                change_log.field_name,
            ),
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
        old_value = cast(
            ChangeLogValue,
            TASK_CHANGE_LOG_FORMATTER.extract_change_value(
                change_log.old_value,
                field_name,
                users_by_id=users_by_id,
                value_formatters=value_formatters,
            ),
        )
        new_value = cast(
            ChangeLogValue,
            TASK_CHANGE_LOG_FORMATTER.extract_change_value(
                change_log.new_value,
                field_name,
                users_by_id=users_by_id,
                value_formatters=value_formatters,
            ),
        )
        new_value = self._with_legacy_created_comment_body(
            change_log,
            new_value,
            comments_by_id,
        )
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
            old_value=old_value,
            new_value=new_value,
            reason=change_log.reason,
            created_by=change_log.changed_by,
            created_by_user=(
                users_by_id.get(change_log.changed_by)
                if change_log.changed_by is not None
                else None
            ),
            created_at=change_log.changed_at,
        )

    def _build_task_response(
        self,
        task: Task,
        *,
        presentation_data: TaskPresentationData,
    ) -> TaskRead:
        """関連要件と表示補助値を含むタスクレスポンスを組み立てる。"""
        task_read = TaskRead.model_validate(task)
        task_read.is_overdue = (
            task.due_date is not None
            and task.due_date < date.today()
            and task.status not in {"done", "cancelled"}
        )
        task_read.is_blocked = task.id in presentation_data.blocked_task_ids
        task_read.requirements = [
            TaskRequirementSummary.model_validate(requirement)
            for requirement in presentation_data.requirements_by_task_id.get(
                task.id,
                [],
            )
        ]
        return task_read

    def _build_change_log_user(self, user: User) -> ChangeLogUserRead:
        """変更履歴の変更者として返す軽量ユーザー情報を組み立てる。"""
        return ChangeLogUserRead(
            id=user.id,
            name=user.name,
            email=user.email,
            avatar_url=None,
        )

    def _with_legacy_created_comment_body(
        self,
        change_log: TaskChangeLog,
        new_value: ChangeLogValue,
        comments_by_id: dict[int, TaskComment],
    ) -> ChangeLogValue:
        """本文未保存の古いコメント作成履歴に本文を補完する。"""
        if (
            change_log.target_type != TaskTargetType.COMMENT
            or change_log.action != TaskChangeLogAction.COMMENT_CREATED
            or not isinstance(new_value, dict)
            or "body" in new_value
        ):
            return new_value

        comment = comments_by_id.get(change_log.target_id)
        if comment is None:
            return new_value
        legacy_value = cast(dict[str, object], new_value)
        return {**legacy_value, "body": comment.body}

    def _get_task_id_from_comment_log(self, change_log: TaskChangeLog) -> int:
        """コメント履歴のレスポンス用タスクIDを取得する。"""
        task_id = None
        if change_log.new_value is not None:
            task_id = change_log.new_value.get("task_id")
        if task_id is None and change_log.old_value is not None:
            task_id = change_log.old_value.get("task_id")
        return task_id if isinstance(task_id, int) else change_log.target_id

    def _normalize_change_log_action(self, action: str) -> TaskChangeLogActionCode:
        """DB保存済みの操作種別をAPI用の安定コードに変換する。"""
        return cast(
            TaskChangeLogActionCode,
            TASK_CHANGE_LOG_FORMATTER.normalize_action(action),
        )

    def _normalize_change_log_target_type(
        self,
        target_type: str,
    ) -> TaskChangeLogTargetTypeCode:
        """DB保存済みの対象種別をAPI用の安定コードに変換する。"""
        return cast(
            TaskChangeLogTargetTypeCode,
            TASK_CHANGE_LOG_FORMATTER.normalize_target_type(target_type),
        )

    def _format_task_id_label_value(
        self,
        value: Any,
        tasks_by_id: dict[int, Task],
    ) -> dict[str, Any] | None:
        """タスクIDをid/label形式に変換する。"""
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
        """要件IDを表示補助付きの配列に変換する。"""
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
        """単一の要件値を表示補助付きに変換する。"""
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
