"""タスクボードのビジネスロジックを提供するService。"""

from datetime import date
from typing import Any, TypedDict

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import NotFoundError
from app.models.task import Board, BoardColumn, Task
from app.repositories.project import ProjectRepository
from app.repositories.task_board import BoardColumnRepository, BoardRepository
from app.repositories.task_change_log import TaskChangeLogRepository
from app.repositories.task_item import TaskRepository
from app.schemas.task import (
    BoardColumnCreate,
    BoardColumnUpdate,
    BoardCreate,
    BoardUpdate,
    TaskMoveRequest,
)
from app.services.conflict import (
    build_conflict_current,
    raise_duplicate_after_rollback,
    raise_if_version_conflict,
)
from app.services.task_presentation import TaskPresentationDataService

BOARD_CONFLICT_CURRENT_FIELDS = (
    "name",
    "description",
    "board_type",
    "id",
    "project_id",
    "version",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
)
BOARD_COLUMN_CONFLICT_CURRENT_FIELDS = (
    "name",
    "status_key",
    "sort_order",
    "wip_limit",
    "is_done_column",
    "id",
    "board_id",
    "version",
    "created_at",
    "updated_at",
)


class TaskDateNormalization(TypedDict):
    """状態変更時に補完したタスク日付・進捗値。"""

    progress_percent: int
    actual_start_date: date | None
    actual_end_date: date | None


class TaskBoardAction:
    """ボード関連変更履歴の操作種別定数。"""

    BOARD_CREATED = "board.created"
    BOARD_UPDATED = "board.updated"
    BOARD_DELETED = "board.deleted"
    COLUMN_CREATED = "column.created"
    COLUMN_UPDATED = "column.updated"
    COLUMN_DELETED = "column.deleted"
    TASK_MOVED = "moved"


class TaskBoardTargetType:
    """ボード関連変更履歴の対象種別定数。"""

    TASK = "task"
    BOARD = "board"
    COLUMN = "board_column"


class TaskBoardService:
    """タスクボードとボード列のビジネスロジックを提供する。"""

    def __init__(
        self,
        *,
        task_repository: TaskRepository | None = None,
        board_repository: BoardRepository | None = None,
        column_repository: BoardColumnRepository | None = None,
        project_repository: ProjectRepository | None = None,
        change_log_repository: TaskChangeLogRepository | None = None,
        presentation_data_service: TaskPresentationDataService | None = None,
    ) -> None:
        """TaskBoardServiceを初期化する。"""
        self.task_repository = task_repository or TaskRepository()
        self.board_repository = board_repository or BoardRepository()
        self.column_repository = column_repository or BoardColumnRepository()
        self.project_repository = project_repository or ProjectRepository()
        self.change_log_repository = change_log_repository or TaskChangeLogRepository()
        self.presentation_data_service = (
            presentation_data_service or TaskPresentationDataService(
                task_repository=self.task_repository,
            )
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
            target_type=TaskBoardTargetType.BOARD,
            target_id=board.id,
            action=TaskBoardAction.BOARD_CREATED,
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
            current=build_conflict_current(board, BOARD_CONFLICT_CURRENT_FIELDS),
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
            target_type=TaskBoardTargetType.BOARD,
            target_id=board.id,
            action=TaskBoardAction.BOARD_UPDATED,
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
            target_type=TaskBoardTargetType.BOARD,
            target_id=board.id,
            action=TaskBoardAction.BOARD_DELETED,
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
            target_type=TaskBoardTargetType.COLUMN,
            target_id=column.id,
            action=TaskBoardAction.COLUMN_CREATED,
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
            current=build_conflict_current(
                column,
                BOARD_COLUMN_CONFLICT_CURRENT_FIELDS,
            ),
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
            target_type=TaskBoardTargetType.COLUMN,
            target_id=column.id,
            action=TaskBoardAction.COLUMN_UPDATED,
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
            target_type=TaskBoardTargetType.COLUMN,
            target_id=column.id,
            action=TaskBoardAction.COLUMN_DELETED,
            changed_by=actor_id,
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
        task = self._get_task(db, task_id)
        if task.project_id != board.project_id:
            raise NotFoundError(error_messages.TASK_NOT_FOUND)
        raise_if_version_conflict(
            current_version=task.version,
            requested_version=move_in.version,
            current=self._build_task_conflict_current(db, task),
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
            target_type=TaskBoardTargetType.TASK,
            target_id=task.id,
            action=TaskBoardAction.TASK_MOVED,
            old_value=old_values,
            new_value={"status": task.status, "sort_order": task.sort_order},
            changed_by=actor_id,
        )
        return task

    def _ensure_project_exists(self, db: Session, project_id: int) -> None:
        """プロジェクトが存在することを確認する。"""
        if self.project_repository.get_by_id(db, project_id) is None:
            raise NotFoundError(error_messages.PROJECT_NOT_FOUND)

    def _get_task(self, db: Session, task_id: int) -> Task:
        """タスクを取得する。"""
        task = self.task_repository.get_by_id(db, task_id)
        if task is None:
            raise NotFoundError(error_messages.TASK_NOT_FOUND)
        return task

    def _build_task_conflict_current(
        self,
        db: Session,
        task: Task,
    ) -> dict[str, Any]:
        """排他制御エラーで返す現行タスク値を組み立てる。"""
        presentation_data = self.presentation_data_service.get_task_presentation_data(
            db,
            [task],
        )
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
            "is_overdue": self._is_overdue(task),
            "is_blocked": task.id in presentation_data.blocked_task_ids,
            "requirements": presentation_data.requirements_by_task_id.get(
                task.id,
                [],
            ),
        }

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

    def _is_overdue(self, task: Task) -> bool:
        """タスクが期限超過しているか判定する。"""
        return (
            task.due_date is not None
            and task.due_date < date.today()
            and task.status not in {"done", "cancelled"}
        )

    def _record_change(
        self,
        db: Session,
        *,
        project_id: int,
        target_type: str,
        target_id: int,
        action: str,
        old_value: dict | None = None,
        new_value: dict | None = None,
        changed_by: int | None = None,
    ) -> None:
        """ボード関連の変更履歴を記録する。"""
        self.change_log_repository.create(
            db,
            project_id=project_id,
            target_type=target_type,
            target_id=target_id,
            action=action,
            old_value=old_value,
            new_value=new_value,
            changed_by=changed_by,
        )
