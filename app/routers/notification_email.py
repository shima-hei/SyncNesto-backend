"""社内/外部の本人に共通した通知メール設定。"""

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.routers.guest_invitations import InvitationRoute
from app.schemas.notification_email import (
    NotificationEmailPreferences,
    NotificationEmailPreferencesUpdate,
)
from app.services.notification_email_preferences import (
    NotificationEmailPreferencesService,
)

router = APIRouter(
    prefix="/notification-preferences",
    tags=["notification-preferences"],
    route_class=InvitationRoute,
)
service = NotificationEmailPreferencesService()


@router.get("", response_model=NotificationEmailPreferences)
def get_notification_email_preferences(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """自分の設定だけを取得する。"""
    return service.get(db, user)


@router.patch("", response_model=NotificationEmailPreferences)
def update_notification_email_preferences(
    data: NotificationEmailPreferencesUpdate,
    expected_user: int = Header(alias="X-Syncnesto-Expected-User", ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """自分の通知先へ結び付ける選択を保存する。"""
    return service.update(db, user, data, expected_user)
