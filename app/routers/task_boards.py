"""タスクボードAPIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.routers.tasks_shared import (
    task_board_service,
    task_presentation_data_service,
    task_presenter,
    task_service,
)
from app.schemas.task import (
    BoardColumnCreate,
    BoardColumnRead,
    BoardColumnUpdate,
    BoardCreate,
    BoardRead,
    BoardUpdate,
    TaskMoveRequest,
    TaskRead,
)

router = APIRouter(tags=["tasks"])


@router.get("/projects/{project_id}/boards", response_model=list[BoardRead])
def list_boards(
    project_id: int,
    _: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
) -> list[BoardRead]:
    """プロジェクト内ボード一覧を取得する。"""
    return task_presenter.build_board_responses(
        task_board_service.list_boards(db, project_id)
    )


@router.post(
    "/projects/{project_id}/boards",
    response_model=BoardRead,
    status_code=status.HTTP_201_CREATED,
)
def create_board(
    project_id: int,
    board_in: BoardCreate,
    current_user: User = Depends(require_project_permission("task:update")),
    db: Session = Depends(get_db),
) -> BoardRead:
    """プロジェクト内にボードを作成する。"""
    board = task_board_service.create_board(
        db,
        project_id=project_id,
        board_in=board_in,
        actor_id=current_user.id,
    )
    return task_presenter.build_board_response(board)


@router.get("/boards/{board_id}", response_model=BoardRead)
def read_board(
    board_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoardRead:
    """ボード詳細を取得する。"""
    board = task_board_service.get_board(db, board_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=board.project_id,
        permission_code="task:read",
    )
    return task_presenter.build_board_response(board)


@router.patch("/boards/{board_id}", response_model=BoardRead)
def update_board(
    board_id: int,
    board_in: BoardUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoardRead:
    """ボードを更新する。"""
    board = task_board_service.get_board(db, board_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=board.project_id,
        permission_code="task:update",
    )
    board = task_board_service.update_board(
        db,
        board_id=board_id,
        board_in=board_in,
        actor_id=current_user.id,
    )
    return task_presenter.build_board_response(board)


@router.delete("/boards/{board_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_board(
    board_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """ボードを論理削除する。"""
    board = task_board_service.get_board(db, board_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=board.project_id,
        permission_code="task:update",
    )
    task_board_service.delete_board(db, board_id=board_id, actor_id=current_user.id)


@router.get("/boards/{board_id}/columns", response_model=list[BoardColumnRead])
def list_board_columns(
    board_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[BoardColumnRead]:
    """ボード列一覧を取得する。"""
    board = task_board_service.get_board(db, board_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=board.project_id,
        permission_code="task:read",
    )
    return task_presenter.build_board_column_responses(
        task_board_service.list_board_columns(db, board_id)
    )


@router.post(
    "/boards/{board_id}/columns",
    response_model=BoardColumnRead,
    status_code=status.HTTP_201_CREATED,
)
def create_board_column(
    board_id: int,
    column_in: BoardColumnCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoardColumnRead:
    """ボード列を作成する。"""
    board = task_board_service.get_board(db, board_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=board.project_id,
        permission_code="task:update",
    )
    column = task_board_service.create_board_column(
        db,
        board_id=board_id,
        column_in=column_in,
        actor_id=current_user.id,
    )
    return task_presenter.build_board_column_response(column)


@router.patch("/board-columns/{column_id}", response_model=BoardColumnRead)
def update_board_column(
    column_id: int,
    column_in: BoardColumnUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoardColumnRead:
    """ボード列を更新する。"""
    column = task_board_service.get_board_column(db, column_id)
    board = task_board_service.get_board(db, column.board_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=board.project_id,
        permission_code="task:update",
    )
    column = task_board_service.update_board_column(
        db,
        column_id=column_id,
        column_in=column_in,
        actor_id=current_user.id,
    )
    return task_presenter.build_board_column_response(column)


@router.delete("/board-columns/{column_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_board_column(
    column_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """ボード列を論理削除する。"""
    column = task_board_service.get_board_column(db, column_id)
    board = task_board_service.get_board(db, column.board_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=board.project_id,
        permission_code="task:update",
    )
    task_board_service.delete_board_column(
        db,
        column_id=column_id,
        actor_id=current_user.id,
    )


@router.post("/boards/{board_id}/tasks/{task_id}/move", response_model=TaskRead)
def move_task(
    board_id: int,
    task_id: int,
    move_in: TaskMoveRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskRead:
    """ボード上でタスクを移動する。"""
    board = task_board_service.get_board(db, board_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=board.project_id,
        permission_code="task:update",
    )
    task = task_board_service.move_task(
        db,
        board_id=board_id,
        task_id=task_id,
        move_in=move_in,
        actor_id=current_user.id,
    )
    return task_presenter.build_task_response(
        task,
        presentation_data=task_presentation_data_service.get_task_presentation_data(
            db,
            [task],
        ),
    )
