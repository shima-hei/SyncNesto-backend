"""タスク管理APIのルーティングを定義するモジュール。"""

from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.task import (
    BoardColumnCreate,
    BoardColumnRead,
    BoardColumnUpdate,
    BoardCreate,
    BoardRead,
    BoardUpdate,
    GanttResponse,
    MilestoneCreate,
    MilestoneRead,
    MilestoneUpdate,
    RequirementTaskCreate,
    RequirementTaskProgressRead,
    RequirementTaskRelationCreate,
    RequirementTaskRelationRead,
    TaskChangeLogListResponse,
    TaskCommentCreate,
    TaskCommentRead,
    TaskCommentStateUpdate,
    TaskCommentUpdate,
    TaskCreate,
    TaskDependencyCreate,
    TaskDependencyRead,
    TaskDependencyUpdate,
    TaskListResponse,
    TaskMoveRequest,
    TaskRead,
    TaskTagListResponse,
    TaskUpdate,
)
from app.services.task import TaskService

router = APIRouter(tags=["tasks"])
task_service = TaskService()


def build_task_response(db: Session, task) -> TaskRead:
    """タスクレスポンスを作成する。

    Args:
        db: DBセッション。
        task: タスクモデル。

    Returns:
        タスクレスポンス。
    """
    return task_service.build_task_read(db, task)


def build_task_responses(db: Session, tasks: list) -> list[TaskRead]:
    """タスク一覧レスポンスを作成する。

    Args:
        db: DBセッション。
        tasks: タスクモデル一覧。

    Returns:
        タスクレスポンス一覧。
    """
    return task_service.build_task_reads(db, tasks)


def build_requirement_task_relation_response(relation) -> RequirementTaskRelationRead:
    """要件タスク関連レスポンスを作成する。"""
    return RequirementTaskRelationRead.model_validate(relation)


def build_task_dependency_response(dependency) -> TaskDependencyRead:
    """タスク依存関係レスポンスを作成する。"""
    return TaskDependencyRead.model_validate(dependency)


def build_task_dependency_responses(dependencies: list) -> list[TaskDependencyRead]:
    """タスク依存関係一覧レスポンスを作成する。"""
    return [
        build_task_dependency_response(dependency) for dependency in dependencies
    ]


def build_board_response(board) -> BoardRead:
    """ボードレスポンスを作成する。"""
    return BoardRead.model_validate(board)


def build_board_responses(boards: list) -> list[BoardRead]:
    """ボード一覧レスポンスを作成する。"""
    return [build_board_response(board) for board in boards]


def build_board_column_response(column) -> BoardColumnRead:
    """ボード列レスポンスを作成する。"""
    return BoardColumnRead.model_validate(column)


def build_board_column_responses(columns: list) -> list[BoardColumnRead]:
    """ボード列一覧レスポンスを作成する。"""
    return [build_board_column_response(column) for column in columns]


def build_milestone_response(milestone) -> MilestoneRead:
    """マイルストーンレスポンスを作成する。"""
    return MilestoneRead.model_validate(milestone)


def build_milestone_responses(milestones: list) -> list[MilestoneRead]:
    """マイルストーン一覧レスポンスを作成する。"""
    return [build_milestone_response(milestone) for milestone in milestones]


@router.get(
    "/projects/{project_id}/tasks",
    response_model=TaskListResponse,
)
def list_tasks(
    project_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    assignee_id: int | None = Query(default=None),
    requirement_id: int | None = Query(default=None),
    parent_task_id: int | None = Query(default=None),
    root_only: bool | None = Query(default=None),
    start_date_from: date | None = Query(default=None),
    due_date_to: date | None = Query(default=None),
    overdue: bool | None = Query(default=None),
    task_type: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    q: str | None = Query(default=None),
    _: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
) -> TaskListResponse:
    """プロジェクト内タスク一覧を取得する。"""
    tasks, total = task_service.list_tasks(
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
    return TaskListResponse(
        items=build_task_responses(db, tasks),
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/projects/{project_id}/tasks/tags",
    response_model=TaskTagListResponse,
)
def list_task_tags(
    project_id: int,
    _: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
) -> TaskTagListResponse:
    """プロジェクト内で利用済みのタスクタグ候補を取得する。"""
    return TaskTagListResponse(items=task_service.list_task_tags(db, project_id))


@router.post(
    "/projects/{project_id}/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
)
def create_task(
    project_id: int,
    task_in: TaskCreate,
    current_user: User = Depends(require_project_permission("task:create")),
    db: Session = Depends(get_db),
) -> TaskRead:
    """プロジェクト内にタスクを作成する。"""
    task = task_service.create_task(
        db,
        project_id=project_id,
        task_in=task_in,
        actor_id=current_user.id,
    )
    return build_task_response(db, task)


@router.get("/tasks/{task_id}", response_model=TaskRead)
def read_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskRead:
    """タスク詳細を取得する。"""
    task = task_service.get_task(db, task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:read",
    )
    return build_task_response(db, task)


@router.patch("/tasks/{task_id}", response_model=TaskRead)
def update_task(
    task_id: int,
    task_in: TaskUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskRead:
    """タスクを更新する。"""
    task = task_service.get_task(db, task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:update",
    )
    task = task_service.update_task(
        db,
        task_id=task_id,
        task_in=task_in,
        actor_id=current_user.id,
    )
    return build_task_response(db, task)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """タスクを論理削除する。"""
    task = task_service.get_task(db, task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:delete",
    )
    task_service.delete_task(db, task_id=task_id, actor_id=current_user.id)


@router.get("/tasks/{task_id}/comments", response_model=list[TaskCommentRead])
def list_task_comments(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TaskCommentRead]:
    """タスクコメント一覧を取得する。"""
    task = task_service.get_task(db, task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:read",
    )
    return task_service.list_comment_reads(db, task_id)


@router.post(
    "/tasks/{task_id}/comments",
    response_model=TaskCommentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_task_comment(
    task_id: int,
    comment_in: TaskCommentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskCommentRead:
    """タスクコメントを作成する。"""
    task = task_service.get_task(db, task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:comment",
    )
    comment = task_service.create_comment(
        db,
        task_id=task_id,
        comment_in=comment_in,
        actor_id=current_user.id,
    )
    return task_service.build_comment_read(db, comment)


@router.patch("/task-comments/{comment_id}", response_model=TaskCommentRead)
def update_task_comment(
    comment_id: int,
    comment_in: TaskCommentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskCommentRead:
    """タスクコメントを更新する。"""
    comment = task_service.get_comment(db, comment_id)
    task = task_service.get_task(db, comment.task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:comment",
    )
    comment = task_service.update_comment(
        db,
        comment_id=comment_id,
        comment_in=comment_in,
        actor_id=current_user.id,
    )
    return task_service.build_comment_read(db, comment)


@router.delete("/task-comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task_comment(
    comment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """タスクコメントを論理削除する。"""
    comment = task_service.get_comment(db, comment_id)
    task = task_service.get_task(db, comment.task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:comment",
    )
    task_service.delete_comment(
        db,
        comment_id=comment_id,
        actor_id=current_user.id,
    )


@router.post("/task-comments/{comment_id}/resolve", response_model=TaskCommentRead)
def resolve_task_comment(
    comment_id: int,
    comment_in: TaskCommentStateUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskCommentRead:
    """タスクコメントを解決済みにする。"""
    comment = task_service.get_comment(db, comment_id)
    task = task_service.get_task(db, comment.task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:comment",
    )
    comment = task_service.resolve_comment(
        db,
        comment_id=comment_id,
        comment_in=comment_in,
        actor_id=current_user.id,
    )
    return task_service.build_comment_read(db, comment)


@router.post("/task-comments/{comment_id}/reopen", response_model=TaskCommentRead)
def reopen_task_comment(
    comment_id: int,
    comment_in: TaskCommentStateUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskCommentRead:
    """タスクコメントを未解決に戻す。"""
    comment = task_service.get_comment(db, comment_id)
    task = task_service.get_task(db, comment.task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:comment",
    )
    comment = task_service.reopen_comment(
        db,
        comment_id=comment_id,
        comment_in=comment_in,
        actor_id=current_user.id,
    )
    return task_service.build_comment_read(db, comment)


@router.get(
    "/tasks/{task_id}/change-logs",
    response_model=TaskChangeLogListResponse,
)
def list_task_change_logs(
    task_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskChangeLogListResponse:
    """タスク変更履歴一覧を取得する。"""
    task = task_service.get_task(db, task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:read",
    )
    items, total = task_service.list_task_change_logs(
        db,
        task_id=task_id,
        page=page,
        page_size=page_size,
    )
    return TaskChangeLogListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/requirements/{requirement_id}/tasks", response_model=list[TaskRead])
def list_requirement_tasks(
    requirement_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TaskRead]:
    """要件に紐づくタスク一覧を取得する。"""
    project_id = task_service.get_requirement_project_id(db, requirement_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=project_id,
        permission_code="task:read",
    )
    tasks = task_service.list_requirement_tasks(db, requirement_id)
    return build_task_responses(db, tasks)


@router.post(
    "/requirements/{requirement_id}/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
)
def create_requirement_task(
    requirement_id: int,
    task_in: RequirementTaskCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskRead:
    """要件に紐づくタスクを作成する。"""
    project_id = task_service.get_requirement_project_id(db, requirement_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=project_id,
        permission_code="task:create",
    )
    task = task_service.create_requirement_task(
        db,
        requirement_id=requirement_id,
        task_in=task_in,
        actor_id=current_user.id,
    )
    return build_task_response(db, task)


@router.get(
    "/requirements/{requirement_id}/task-progress",
    response_model=RequirementTaskProgressRead,
)
def read_requirement_task_progress(
    requirement_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RequirementTaskProgressRead:
    """要件に紐づくタスク進捗を取得する。"""
    project_id = task_service.get_requirement_project_id(db, requirement_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=project_id,
        permission_code="task:read",
    )
    return task_service.get_requirement_progress(db, requirement_id)


@router.post(
    "/requirements/{requirement_id}/task-relations",
    response_model=RequirementTaskRelationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_requirement_task_relation(
    requirement_id: int,
    relation_in: RequirementTaskRelationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RequirementTaskRelationRead:
    """要件タスク関連を作成する。"""
    project_id = task_service.get_requirement_project_id(db, requirement_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=project_id,
        permission_code="task:update",
    )
    relation = task_service.create_requirement_task_relation(
        db,
        requirement_id=requirement_id,
        task_id=relation_in.task_id,
        relation_type=relation_in.relation_type,
        actor_id=current_user.id,
    )
    return build_requirement_task_relation_response(relation)


@router.delete(
    "/requirements/{requirement_id}/task-relations/{relation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_requirement_task_relation(
    requirement_id: int,
    relation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """要件タスク関連を削除する。"""
    project_id = task_service.get_requirement_project_id(db, requirement_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=project_id,
        permission_code="task:update",
    )
    task_service.delete_requirement_task_relation(
        db,
        requirement_id=requirement_id,
        relation_id=relation_id,
        actor_id=current_user.id,
    )


@router.get("/tasks/{task_id}/dependencies", response_model=list[TaskDependencyRead])
def list_task_dependencies(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TaskDependencyRead]:
    """タスク依存関係一覧を取得する。"""
    task = task_service.get_task(db, task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:read",
    )
    return build_task_dependency_responses(
        task_service.list_dependencies(db, task_id)
    )


@router.post(
    "/tasks/{task_id}/dependencies",
    response_model=TaskDependencyRead,
    status_code=status.HTTP_201_CREATED,
)
def create_task_dependency(
    task_id: int,
    dependency_in: TaskDependencyCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskDependencyRead:
    """タスク依存関係を作成する。"""
    task = task_service.get_task(db, task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:update",
    )
    if task_id != dependency_in.successor_task_id:
        dependency_in = dependency_in.model_copy(update={"successor_task_id": task_id})
    dependency = task_service.create_dependency(
        db,
        dependency_in=dependency_in,
        actor_id=current_user.id,
    )
    return build_task_dependency_response(dependency)


@router.patch(
    "/task-dependencies/{dependency_id}",
    response_model=TaskDependencyRead,
)
def update_task_dependency(
    dependency_id: int,
    dependency_in: TaskDependencyUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TaskDependencyRead:
    """タスク依存関係を更新する。"""
    dependency = task_service.get_dependency(db, dependency_id)
    task = task_service.get_task(db, dependency.successor_task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:update",
    )
    dependency = task_service.update_dependency(
        db,
        dependency_id=dependency_id,
        dependency_in=dependency_in,
        actor_id=current_user.id,
    )
    return build_task_dependency_response(dependency)


@router.delete(
    "/task-dependencies/{dependency_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_task_dependency(
    dependency_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """タスク依存関係を削除する。"""
    dependency = task_service.get_dependency(db, dependency_id)
    task = task_service.get_task(db, dependency.successor_task_id)
    task_service.ensure_user_can_access_task(
        db,
        user=current_user,
        task=task,
        permission_code="task:update",
    )
    task_service.delete_dependency(
        db,
        dependency_id=dependency_id,
        actor_id=current_user.id,
    )


@router.get("/projects/{project_id}/boards", response_model=list[BoardRead])
def list_boards(
    project_id: int,
    _: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
) -> list[BoardRead]:
    """プロジェクト内ボード一覧を取得する。"""
    return build_board_responses(task_service.list_boards(db, project_id))


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
    board = task_service.create_board(
        db,
        project_id=project_id,
        board_in=board_in,
        actor_id=current_user.id,
    )
    return build_board_response(board)


@router.get("/boards/{board_id}", response_model=BoardRead)
def read_board(
    board_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoardRead:
    """ボード詳細を取得する。"""
    board = task_service.get_board(db, board_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=board.project_id,
        permission_code="task:read",
    )
    return build_board_response(board)


@router.patch("/boards/{board_id}", response_model=BoardRead)
def update_board(
    board_id: int,
    board_in: BoardUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoardRead:
    """ボードを更新する。"""
    board = task_service.get_board(db, board_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=board.project_id,
        permission_code="task:update",
    )
    board = task_service.update_board(
        db,
        board_id=board_id,
        board_in=board_in,
        actor_id=current_user.id,
    )
    return build_board_response(board)


@router.delete("/boards/{board_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_board(
    board_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """ボードを論理削除する。"""
    board = task_service.get_board(db, board_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=board.project_id,
        permission_code="task:update",
    )
    task_service.delete_board(db, board_id=board_id, actor_id=current_user.id)


@router.get("/boards/{board_id}/columns", response_model=list[BoardColumnRead])
def list_board_columns(
    board_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[BoardColumnRead]:
    """ボード列一覧を取得する。"""
    board = task_service.get_board(db, board_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=board.project_id,
        permission_code="task:read",
    )
    return build_board_column_responses(
        task_service.list_board_columns(db, board_id)
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
    board = task_service.get_board(db, board_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=board.project_id,
        permission_code="task:update",
    )
    column = task_service.create_board_column(
        db,
        board_id=board_id,
        column_in=column_in,
        actor_id=current_user.id,
    )
    return build_board_column_response(column)


@router.patch("/board-columns/{column_id}", response_model=BoardColumnRead)
def update_board_column(
    column_id: int,
    column_in: BoardColumnUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BoardColumnRead:
    """ボード列を更新する。"""
    column = task_service.get_board_column(db, column_id)
    board = task_service.get_board(db, column.board_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=board.project_id,
        permission_code="task:update",
    )
    column = task_service.update_board_column(
        db,
        column_id=column_id,
        column_in=column_in,
        actor_id=current_user.id,
    )
    return build_board_column_response(column)


@router.delete("/board-columns/{column_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_board_column(
    column_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """ボード列を論理削除する。"""
    column = task_service.get_board_column(db, column_id)
    board = task_service.get_board(db, column.board_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=board.project_id,
        permission_code="task:update",
    )
    task_service.delete_board_column(
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
    board = task_service.get_board(db, board_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=board.project_id,
        permission_code="task:update",
    )
    task = task_service.move_task(
        db,
        board_id=board_id,
        task_id=task_id,
        move_in=move_in,
        actor_id=current_user.id,
    )
    return build_task_response(db, task)


@router.get("/projects/{project_id}/gantt", response_model=GanttResponse)
def read_gantt(
    project_id: int,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    requirement_id: int | None = Query(default=None),
    assignee_id: int | None = Query(default=None),
    _: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
) -> GanttResponse:
    """ガントチャート用データを取得する。"""
    tasks, dependencies, milestones = task_service.get_gantt(
        db,
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        requirement_id=requirement_id,
        assignee_id=assignee_id,
    )
    return GanttResponse(
        tasks=build_task_responses(db, tasks),
        dependencies=[
            build_task_dependency_response(dependency)
            for dependency in dependencies
        ],
        milestones=[
            build_milestone_response(milestone) for milestone in milestones
        ],
    )


@router.get(
    "/projects/{project_id}/milestones",
    response_model=list[MilestoneRead],
)
def list_milestones(
    project_id: int,
    _: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
) -> list[MilestoneRead]:
    """プロジェクト内マイルストーン一覧を取得する。"""
    return build_milestone_responses(
        task_service.list_milestones(db, project_id)
    )


@router.post(
    "/projects/{project_id}/milestones",
    response_model=MilestoneRead,
    status_code=status.HTTP_201_CREATED,
)
def create_milestone(
    project_id: int,
    milestone_in: MilestoneCreate,
    current_user: User = Depends(require_project_permission("task:update")),
    db: Session = Depends(get_db),
) -> MilestoneRead:
    """プロジェクト内にマイルストーンを作成する。"""
    milestone = task_service.create_milestone(
        db,
        project_id=project_id,
        milestone_in=milestone_in,
        actor_id=current_user.id,
    )
    return build_milestone_response(milestone)


@router.get("/milestones/{milestone_id}", response_model=MilestoneRead)
def read_milestone(
    milestone_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MilestoneRead:
    """マイルストーン詳細を取得する。"""
    milestone = task_service.get_milestone(db, milestone_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=milestone.project_id,
        permission_code="task:read",
    )
    return build_milestone_response(milestone)


@router.patch("/milestones/{milestone_id}", response_model=MilestoneRead)
def update_milestone(
    milestone_id: int,
    milestone_in: MilestoneUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MilestoneRead:
    """マイルストーンを更新する。"""
    milestone = task_service.get_milestone(db, milestone_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=milestone.project_id,
        permission_code="task:update",
    )
    milestone = task_service.update_milestone(
        db,
        milestone_id=milestone_id,
        milestone_in=milestone_in,
        actor_id=current_user.id,
    )
    return build_milestone_response(milestone)


@router.delete("/milestones/{milestone_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_milestone(
    milestone_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """マイルストーンを論理削除する。"""
    milestone = task_service.get_milestone(db, milestone_id)
    task_service.ensure_user_can_access_project_resource(
        db,
        user=current_user,
        project_id=milestone.project_id,
        permission_code="task:update",
    )
    task_service.delete_milestone(
        db,
        milestone_id=milestone_id,
        actor_id=current_user.id,
    )
