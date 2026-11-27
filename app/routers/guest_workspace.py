"""公開用の案件概要と本人の案件一覧。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.tenant import require_project_management
from app.db.session import get_db
from app.models.user import User
from app.schemas.guest_activity import GuestActivityPage, GuestActivityRead
from app.schemas.guest_workspace import (
    GuestProjectList,
    GuestProjectProfileSettings,
    GuestProjectRead,
)
from app.schemas.shared_publication_comment import GuestCommentList
from app.services.guest_activity import GuestActivityService
from app.services.guest_workspace import GuestWorkspaceService
from app.services.shared_publication_comment import SharedPublicationCommentService

router = APIRouter(
    prefix="/projects/{project_id}/guest-profile", tags=["guest-project-profile"]
)
shared_router = APIRouter(prefix="/shared/projects", tags=["shared-projects"])
comment_router = APIRouter(prefix="/shared/comments", tags=["shared-comments"])
activity_router = APIRouter(prefix="/shared/activity", tags=["shared-activity"])
Db = Annotated[Session, Depends(get_db)]
Guest = Annotated[User, Depends(get_current_user)]
Manager = Annotated[User, Depends(require_project_management("project:update"))]
service = GuestWorkspaceService()
comments = SharedPublicationCommentService()
activity = GuestActivityService()


@activity_router.get("", response_model=GuestActivityPage)
def list_guest_activity(
    db: Db,
    user: Guest,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    unread_only: bool = False,
    comments_only: bool = False,
):
    """現在閲覧できる通知を返す。コメント通知に絞り込める。"""
    return activity.listing(db, user, page, page_size, unread_only, comments_only)


@activity_router.post("/{notice_id}/read", response_model=GuestActivityRead)
def mark_guest_activity_read(notice_id: UUID, db: Db, user: Guest):
    """現在も受け取り済みの共有版にアクセス可能な本人だけが既読にする。"""
    return activity.mark_read(db, user, notice_id)


@comment_router.get("", response_model=GuestCommentList)
def list_guest_comments(
    db: Db,
    user: Guest,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    participation_id: UUID | None = None,
):
    """本人が閲覧できる固定版の共有コメントを集約する。"""
    return comments.inbox(db, user, page, page_size, participation_id)


@shared_router.get("", response_model=GuestProjectList)
def list_guest_projects(
    db: Db,
    user: Guest,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """現在参加している案件だけを取得する。"""
    return service.projects(db, user, page, page_size)


@shared_router.get("/{participation_id}", response_model=GuestProjectRead)
def get_guest_project(participation_id: UUID, db: Db, user: Guest):
    """公開用に設定した項目だけを取得する。"""
    return service.project(db, user, participation_id)


@router.get("", response_model=GuestProjectProfileSettings)
def get_guest_project_profile(project_id: int, db: Db, user: Manager):
    """社内管理者が公開用の項目を確認する。"""
    return service.settings(db, user, project_id)


@router.put("", response_model=GuestProjectProfileSettings)
def update_guest_project_profile(
    project_id: int, data: GuestProjectProfileSettings, db: Db, user: Manager
):
    """確認した版から、参加者向けの公開概要を設定する。"""
    return service.settings(db, user, project_id, data)
