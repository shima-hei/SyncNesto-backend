"""招待条件と、管理/受信者の公開範囲を分離した契約。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)


class InvitationInput(BaseModel):
    """明示していない値やactorの入力を拒否する。"""

    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class InvitationCreate(InvitationInput):
    """参加期限とメールの宛先だけで始める。"""

    email: EmailStr
    display_label: str = Field(min_length=1, max_length=255)
    participation_expires_at: AwareDatetime

    @field_validator("display_label")
    @classmethod
    def nonblank(cls, value: str) -> str:
        """呼び名を空白だけにしない。"""
        if not value.strip():
            raise ValueError("呼び名を入力してください")
        return value.strip()


class InvitationVersion(InvitationInput):
    """確認時の状態と同一操作を指定する。"""

    version: int = Field(ge=1)


class InvitationRevoke(InvitationVersion):
    """招待取消の理由を保持する。"""

    reason: str = Field(min_length=1, max_length=1000)


class InvitationToken(BaseModel):
    """URLやログに載せないメールtoken。"""

    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=40, max_length=200, repr=False)


class InvitationRegister(InvitationVersion):
    """未登録宛先の本人が初回パスワードを指定する。"""

    name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=12, max_length=128, repr=False)


class InvitationRead(BaseModel):
    """管理者には宛先を返し、他組織の所属や登録状態を返さない。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    display_label: str
    state: str
    version: int
    delivery_state: str
    expires_at: datetime
    participation_expires_at: datetime
    created_at: datetime


class InvitationPage(BaseModel):
    """Project内の招待だけをページングする。"""

    items: list[InvitationRead]
    total: int
    page: int
    page_size: int


class InvitationRecipientRead(BaseModel):
    """メール確認後にも本文/内部ID/所属を返さない。"""

    id: UUID
    organization_name: str
    project_name: str
    display_label: str
    state: str
    version: int
    expires_at: datetime
    participation_expires_at: datetime
    next_step: Literal[
        "register", "login", "mfa", "accept", "accepted", "unavailable", "wrong_account"
    ]
    current_user_name: str | None = None


class InvitationOperation(BaseModel):
    """秘密を含まず、同一操作の再送時にも同じ結果を返す。"""

    id: UUID
    state: str
    version: int
    delivery_state: str | None = None


class InvitationEventRead(BaseModel):
    """原文・メール・確認secretを含まない業務経緯。"""

    id: UUID
    kind: str
    actor_name: str | None
    actor_kind: str
    occurred_at: datetime
    state: str
    reason: str | None


class InvitationEvents(BaseModel):
    """追記順の履歴ページ。"""

    items: list[InvitationEventRead]
    total: int
    page: int
    page_size: int
