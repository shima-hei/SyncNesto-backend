"""共有資料と返信の本人向け通知。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class GuestActivityRead(BaseModel):
    id: UUID
    kind: Literal[
        "publication_shared",
        "shared_comment_reply",
        "shared_comment_mention",
        "requirement_review_requested",
        "requirement_approval_requested",
    ]
    title: str
    publication_id: UUID
    thread_id: UUID | None
    created_at: datetime
    expires_at: datetime
    read_at: datetime | None


class GuestActivityPage(BaseModel):
    items: list[GuestActivityRead]
    total: int
    unread: int
