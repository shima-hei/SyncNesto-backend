"""組織を持たない外部本人の通知専用API。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.routers.guest_invitations import InvitationRoute
from app.schemas.fixed_approval_notification import (
    SharedNotificationPage,
    SharedNotificationRead,
)
from app.schemas.notification import NotificationMarkAllRead, NotificationUnreadCount
from app.services.shared_notification import SharedNotificationService

router = APIRouter(
    prefix="/shared/notifications",
    tags=["shared-notifications"],
    route_class=InvitationRoute,
)
Db = Annotated[Session, Depends(get_db)]
Recipient = Annotated[User, Depends(get_current_user)]
service = SharedNotificationService()


@router.get("", response_model=SharedNotificationPage)
def list_shared_notifications(
    db: Db,
    user: Recipient,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    unread_only: bool = False,
):
    """現在読める本人の通知だけを取得する。"""
    return service.listing(db, user, page, page_size, unread_only)


@router.get("/unread-count", response_model=NotificationUnreadCount)
def shared_notification_unread_count(db: Db, user: Recipient):
    """現在読める本人の未読数を取得する。"""
    return NotificationUnreadCount(count=service.unread_count(db, user))


@router.post("/read-all", response_model=NotificationMarkAllRead)
def mark_shared_notifications_read(db: Db, user: Recipient):
    """現在読める通知だけを既読にする。"""
    count = service.mark_read(db, user)
    assert isinstance(count, int)
    return NotificationMarkAllRead(updated_count=count)


@router.post("/{event_id}/read", response_model=SharedNotificationRead)
def mark_shared_notification_read(event_id: UUID, db: Db, user: Recipient):
    """正本event UUIDで本人の通知だけを既読にする。"""
    return service.mark_read(db, user, event_id)
