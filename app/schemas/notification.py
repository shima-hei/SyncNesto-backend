"""通知一覧、既読管理、遷移情報のAPI契約。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.notification import NotificationTargetType, NotificationType


class NotificationSnapshot(BaseModel):
    """通知発生時の表示情報。"""

    actor_name: str
    project_name: str
    target_title: str
    excerpt: str | None = None


class NotificationContext(BaseModel):
    """親画面を特定するためのID。"""

    document_id: int | None = None
    requirement_id: int | None = None
    task_id: int | None = None
    design_id: int | None = None
    subject_type: str | None = None
    subject_id: str | None = None


class NotificationRead(BaseModel):
    """宛先本人だけが取得できる通知。"""

    id: int
    type: NotificationType
    actor_user_id: int | None
    project_id: int | None
    target_type: NotificationTargetType
    target_id: str
    snapshot: NotificationSnapshot
    context: NotificationContext
    target_status: Literal["available", "deleted", "forbidden"]
    is_read: bool
    read_at: datetime | None
    created_at: datetime


class NotificationListResponse(BaseModel):
    """既存APIと同じページ番号方式の通知一覧。"""

    items: list[NotificationRead]
    total: int
    page: int
    page_size: int


class NotificationUnreadCount(BaseModel):
    """指定範囲の未読件数。"""

    count: int


class NotificationMarkAllRead(BaseModel):
    """一括既読で更新した件数。"""

    updated_count: int
