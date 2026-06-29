"""タスク管理Repositoryを定義するモジュール。"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.task import (
    Board,
    BoardColumn,
)
from app.schemas.task import (
    BoardColumnCreate,
    BoardColumnUpdate,
    BoardCreate,
    BoardUpdate,
)


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
