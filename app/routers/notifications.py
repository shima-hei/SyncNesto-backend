"""認証ユーザー本人の通知だけを操作するAPI。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.notification import (
    NotificationListResponse,
    NotificationMarkAllRead,
    NotificationRead,
    NotificationUnreadCount,
)
from app.services.notification import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])
service = NotificationService()


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    unread_only: bool = False,
    project_id: int | None = Query(default=None, ge=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """全案件または指定案件の自分宛て通知を取得する。"""
    return service.list(
        db,
        user,
        page=page,
        page_size=page_size,
        unread_only=unread_only,
        project_id=project_id,
    )


@router.get("/unread-count", response_model=NotificationUnreadCount)
def unread_count(
    project_id: int | None = Query(default=None, ge=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """自分宛ての未読件数を取得する。"""
    return NotificationUnreadCount(count=service.unread_count(db, user, project_id))


@router.post("/read-all", response_model=NotificationMarkAllRead)
def mark_all_read(
    project_id: int | None = Query(default=None, ge=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """全案件または指定案件の自分宛て通知をすべて既読にする。"""
    return NotificationMarkAllRead(
        updated_count=service.mark_all_read(db, user, project_id)
    )


@router.post("/{notification_id}/read", response_model=NotificationRead)
def mark_notification_read(
    notification_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """指定した自分宛て通知を既読にする。"""
    return service.mark_read(db, user, notification_id)
