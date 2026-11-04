"""既存のTask読取とProject更新権限による種別管理。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.task_type import (
    TaskTypeCreate,
    TaskTypeList,
    TaskTypeRead,
    TaskTypeUpdate,
)
from app.services.task_type import TaskTypeService

router = APIRouter(tags=["task-types"])
service = TaskTypeService()


@router.get("/projects/{project_id}/task-types", response_model=TaskTypeList)
def list_task_types(
    project_id: int,
    user: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
):
    """過去の非表示/自由文字列も表示できるカタログを取得する。"""
    return service.list(db, project_id)


@router.post(
    "/projects/{project_id}/task-types", response_model=TaskTypeRead, status_code=201
)
def create_task_type(
    project_id: int,
    data: TaskTypeCreate,
    user: User = Depends(require_project_permission("project:update")),
    db: Session = Depends(get_db),
):
    """案件の独自種別を作成する。"""
    return service.save(db, project_id, data, user.id)


@router.patch(
    "/projects/{project_id}/task-types/{type_key}", response_model=TaskTypeRead
)
def update_task_type(
    project_id: int,
    type_key: str,
    data: TaskTypeUpdate,
    user: User = Depends(require_project_permission("project:update")),
    db: Session = Depends(get_db),
):
    """名前と選択可否を保存し、使用済みのキーは削除しない。"""
    return service.save(db, project_id, data, user.id, type_key)
