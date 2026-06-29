"""タスク管理関連レスポンスのPresenterを定義するモジュール。"""

from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.models.task import (
    Board,
    BoardColumn,
    Milestone,
    RequirementTaskRelation,
    Task,
    TaskComment,
    TaskDependency,
)
from app.schemas.task import (
    BoardColumnRead,
    BoardRead,
    GanttResponse,
    MilestoneRead,
    RequirementTaskRelationRead,
    TaskCommentRead,
    TaskDependencyRead,
    TaskRead,
)

if TYPE_CHECKING:
    from app.services.task import TaskService


class TaskPresenter:
    """タスク管理APIのレスポンス変換を担当する。"""

    def __init__(self, task_service: "TaskService") -> None:
        """TaskPresenterを初期化する。

        Args:
            task_service: タスクレスポンス補完に使用するService。
        """
        self.task_service = task_service

    def build_task_response(self, db: Session, task: Task) -> TaskRead:
        """タスクレスポンスを組み立てる。

        Args:
            db: DBセッション。
            task: タスクモデル。

        Returns:
            タスクレスポンス。
        """
        return self.task_service.build_task_read(db, task)

    def build_task_responses(
        self,
        db: Session,
        tasks: list[Task],
    ) -> list[TaskRead]:
        """タスク一覧レスポンスを組み立てる。

        Args:
            db: DBセッション。
            tasks: タスクモデル一覧。

        Returns:
            タスクレスポンス一覧。
        """
        return self.task_service.build_task_reads(db, tasks)

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
        db: Session,
        comment: TaskComment,
    ) -> TaskCommentRead:
        """タスクコメントレスポンスを組み立てる。

        Args:
            db: DBセッション。
            comment: タスクコメントモデル。

        Returns:
            タスクコメントレスポンス。
        """
        return self.task_service.build_comment_read(db, comment)

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
        db: Session,
        *,
        tasks: list[Task],
        dependencies: list[TaskDependency],
        milestones: list[Milestone],
    ) -> GanttResponse:
        """ガントチャートレスポンスを組み立てる。

        Args:
            db: DBセッション。
            tasks: タスクモデル一覧。
            dependencies: タスク依存関係モデル一覧。
            milestones: マイルストーンモデル一覧。

        Returns:
            ガントチャートレスポンス。
        """
        return GanttResponse(
            tasks=self.build_task_responses(db, tasks),
            dependencies=self.build_task_dependency_responses(dependencies),
            milestones=self.build_milestone_responses(milestones),
        )
