"""本人別公開の内部操作と、内部対応を含まないshared応答。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.artifact_publication import (
    PublicationContentMetadata,
    PublicationNodePage,
    PublicFile,
)


class AccessInput(BaseModel):
    """未定義項目を受け入れず、操作ごとに再送IDを指定する。"""

    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class PublicationRecipient(BaseModel):
    """参加世代とその確認時version、明示した許可期限。"""

    model_config = ConfigDict(extra="forbid")
    participation_id: UUID
    version: int = Field(ge=1)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value):
        """曖昧なローカル時刻を受け入れない。"""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("タイムゾーン付きの期限を指定してください")
        return value


class PublicationPublish(AccessInput):
    """全文・添付構成と相手・期限を確認して公開する。"""

    expected_state: Literal["prepared", "published"]
    publication_version: int = Field(ge=1)
    public_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed_full_content: Literal[True]
    recipients: list[PublicationRecipient] = Field(min_length=1, max_length=20)

    @field_validator("recipients")
    @classmethod
    def distinct_people(cls, values):
        """同じ世代を重複して許可しない。"""
        if len({v.participation_id for v in values}) != len(values):
            raise ValueError("公開相手が重複しています")
        return values


class PublicationRevoke(AccessInput):
    """確認時versionと理由を保持して停止・失効する。"""

    version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        """空白だけの理由を受け入れない。"""
        if not value.strip():
            raise ValueError("理由を入力してください")
        return value.strip()


class GuestParticipationRead(BaseModel):
    """内部管理向けの参加世代。非公開資料名・件数は含めない。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    display_label: str
    generation: int
    status: Literal["active", "revoked"]
    effective_status: Literal["active", "revoked", "expired"]
    expires_at: datetime
    version: int
    revoked_at: datetime | None
    revocation_reason: str | None


class GuestParticipationList(BaseModel):
    """参加資格のページ。"""

    items: list[GuestParticipationRead]
    total: int
    page: int
    page_size: int


class PublicationGrantRead(BaseModel):
    """内部だけの宛先と許可履歴。"""

    id: UUID
    participation_id: UUID
    display_label: str
    status: Literal["active", "revoked"]
    effective_status: Literal["active", "revoked", "expired"]
    expires_at: datetime
    version: int
    revoked_at: datetime | None
    revocation_reason: str | None


class PublicationGrantList(BaseModel):
    """公開版の許可一覧。"""

    items: list[PublicationGrantRead]
    total: int
    page: int
    page_size: int


class PublicationEventRead(BaseModel):
    """業務上の公開・失効履歴。監査ログと区別する。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event_kind: str
    actor_id: int | None
    actor_label: str | None = None
    actor_kind: Literal["user", "system_guard"]
    occurred_at: datetime
    recorded_at: datetime
    reason: str | None
    target_state: str | None
    target_version: int | None
    public_digest: str | None
    publication_id: UUID | None
    participation_id: UUID | None
    grant_id: UUID | None
    operation_id: UUID | None


class PublicationEventList(BaseModel):
    """公開版に関する追記履歴のページ。"""

    items: list[PublicationEventRead]
    total: int
    page: int
    page_size: int


class PublicationOperationResult(BaseModel):
    """本文を含まず、確定時の操作結果だけを返す。"""

    id: UUID
    state: str
    version: int
    operation_id: UUID


class SharedPublicationRead(BaseModel):
    """認証済み本人へ現在許可された固定本文だけを返す。"""

    content: PublicationContentMetadata
    expires_at: datetime
    authorization_revision: str


class SharedPublicationSummary(BaseModel):
    """一覧に内部ID・作成者・他宛先・組織情報を持ち込まない。"""

    id: UUID
    title: str
    source_kinds: list[str]
    captured_at: datetime
    expires_at: datetime
    authorization_revision: str


class SharedPublicationList(BaseModel):
    """許可された集合だけで検索・件数・cursorを作る。"""

    items: list[SharedPublicationSummary]
    total: int
    next_cursor: str | None


class SharedPublicationNodePage(PublicationNodePage):
    """本文ページにも本人/sessionの認可revisionを付ける。"""

    authorization_revision: str


class SharedPublicationFiles(BaseModel):
    """添付一覧だけ。配信URLは3cで別設計する。"""

    items: list[PublicFile]
    authorization_revision: str
