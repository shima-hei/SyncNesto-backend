"""タスク本体APIのルーティングを定義するモジュール。"""

from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.routers.tasks_shared import (
    task_presentation_data_service,
    task_presenter,
    task_service,
)
from app.schemas.task import (
    GanttResponse,
    RequirementTaskCreate,
    RequirementTaskProgressRead,
    RequirementTaskRelationCreate,
    RequirementTaskRelationRead,
    TaskCreate,
    TaskListResponse,
    TaskRead,
    TaskTagListResponse,
    TaskUpdate,
)

router = APIRouter(tags=["tasks"])


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
    return task_presenter.build_task_list_response(
        tasks=tasks,
        total=total,
        page=page,
        page_size=page_size,
        presentation_data=task_presentation_data_service.get_task_presentation_data(
            db,
            tasks,
        ),
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
    tags = task_service.list_task_tags(db, project_id)
    return task_presenter.build_task_tag_list_response(tags)


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
    return task_presenter.build_task_response(
        task,
        presentation_data=task_presentation_data_service.get_task_presentation_data(
            db,
            [task],
        ),
    )


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
    return task_presenter.build_task_response(
        task,
        presentation_data=task_presentation_data_service.get_task_presentation_data(
            db,
            [task],
        ),
    )


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
    return task_presenter.build_task_response(
        task,
        presentation_data=task_presentation_data_service.get_task_presentation_data(
            db,
            [task],
        ),
    )


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
    return task_presenter.build_task_responses(
        tasks,
        presentation_data=task_presentation_data_service.get_task_presentation_data(
            db,
            tasks,
        ),
    )


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
    return task_presenter.build_task_response(
        task,
        presentation_data=task_presentation_data_service.get_task_presentation_data(
            db,
            [task],
        ),
    )


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
    return task_presenter.build_requirement_task_relation_response(relation)


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
    return task_presenter.build_gantt_response(
        tasks=tasks,
        dependencies=dependencies,
        milestones=milestones,
        presentation_data=task_presentation_data_service.get_task_presentation_data(
            db,
            tasks,
        ),
    )
