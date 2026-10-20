"""共有通知と、正本から導出する社内の次操作。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class SharedNotificationRead(BaseModel):
    """内部通知IDや内部本文を含めない本人向け投影。"""

    id: UUID
    type: Literal["fixed_approval_requested", "fixed_approval_cancelled"]
    approval_id: UUID
    publication_id: UUID
    target_kind: Literal[
        "requirement", "requirement_document", "specification", "test_design"
    ]
    title: str
    requester_name: str
    action: Literal["requested", "cancelled"]
    current_status: Literal[
        "requested", "approved", "approved_with_exceptions", "rejected", "cancelled"
    ]
    can_decide: bool
    is_read: bool
    read_at: datetime | None
    created_at: datetime
    expires_at: datetime
    authorization_revision: str


class SharedNotificationPage(BaseModel):
    """現在認可した集合だけの一覧と件数。"""

    items: list[SharedNotificationRead]
    total: int
    page: int
    page_size: int


class ApprovalNextActor(BaseModel):
    """工程を強制せず、今回の依頼で次に対応する人を示す。"""

    kind: Literal["requester", "approver", "none"]
    name: str | None
    is_you: bool


class ApprovalNextAction(BaseModel):
    """現在の対象/権限を確認した既存画面への入口。"""

    kind: Literal[
        "open_source", "edit_source", "open_fixed_versions", "open_publication"
    ]
    source_kind: (
        Literal["requirement", "requirement_document", "specification", "test_design"]
        | None
    ) = None
    source_id: int | None = None
    document_id: int | None = None
    publication_id: UUID | None = None


class ApprovalNextActions(BaseModel):
    """保存しない現在の状態と次操作。"""

    approval_id: UUID
    status: str
    next_actor: ApprovalNextActor
    actions: list[ApprovalNextAction]
