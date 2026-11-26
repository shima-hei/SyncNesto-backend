"""認証済みユーザー本人のHOME表示API。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.home import (
    HomeProjectsRead,
    HomeRequirementWorkRead,
    HomeTasksRead,
    HomeTimezone,
)
from app.services.home import HomeService

router = APIRouter(prefix="/home", tags=["home"])
service = HomeService()


@router.get("/requirement-work", response_model=HomeRequirementWorkRead)
def read_home_requirement_work(
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=8, ge=1, le=20),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HomeRequirementWorkRead:
    """参加案件から本人のレビュー・正式承認・修正指摘を取得する。"""
    response.headers["Cache-Control"] = "private, no-store"
    return service.requirement_work(db, current_user, page, page_size)


@router.get("/tasks", response_model=HomeTasksRead)
def read_home_tasks(
    timezone: Annotated[HomeTimezone, Query(max_length=64)] = "Asia/Tokyo",
    limit: int = Query(default=8, ge=1, le=10),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HomeTasksRead:
    """参加案件内の本人の未完了タスクと件数を返す。"""
    return service.tasks(db, current_user, timezone, limit)


@router.get("/projects", response_model=HomeProjectsRead)
def read_home_projects(
    timezone: Annotated[HomeTimezone, Query(max_length=64)] = "Asia/Tokyo",
    limit: int = Query(default=8, ge=1, le=10),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HomeProjectsRead:
    """参加案件の総数と権限のあるタスク集計を返す。"""
    return service.projects(db, current_user, timezone, limit)
