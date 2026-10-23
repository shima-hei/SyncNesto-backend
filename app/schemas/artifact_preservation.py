"""保存キーやleaseを外へ返さない、内部向け添付保全契約。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PreservationSource(BaseModel):
    """同じProjectの既存資料添付を選ぶ。"""

    model_config = ConfigDict(extra="forbid")
    document_id: int = Field(gt=0)
    attachment_id: UUID


class PreservationPreviewRequest(BaseModel):
    """元ファイルの識別子だけを受け取る。"""

    model_config = ConfigDict(extra="forbid")
    attachments: list[PreservationSource] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_attachments(self):
        """同じ元添付を二重に含めない。"""
        ids = [a.attachment_id for a in self.attachments]
        if len(ids) != len(set(ids)):
            raise ValueError("添付が重複しています")
        return self


class PreservationCreateRequest(PreservationPreviewRequest):
    """確認済みの選択と再送識別子で準備を予約する。"""

    request_id: UUID
    expected_plan_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class PreservationFile(BaseModel):
    """選択時の表示属性と、保全が終わった実体の内容hash。"""

    file_id: UUID
    document_id: int
    attachment_id: UUID
    document_version: int
    filename: str
    content_type: str
    byte_size: int
    copied: bool
    content_digest: str | None = None
    preserved_at: datetime | None = None


class PreservationPreview(BaseModel):
    """bytesのhashとは区別する、選択構成の確認結果。"""

    release_id: UUID
    plan_digest: str
    files: list[PreservationFile]
    total_bytes: int


class PreservationRead(BaseModel):
    """固定本文と添付構成の状態。完成まではDLできない。"""

    id: UUID
    release_id: UUID
    status: Literal["preparing", "ready", "failed", "cancelled"]
    created_by: int
    created_at: datetime
    completed_at: datetime | None
    plan_digest: str
    manifest_digest: str | None
    files: list[PreservationFile]
    copied_count: int
    cleanup_pending: int
    error_code: str | None


class PreservationList(BaseModel):
    """同じ認可範囲の件数と構成一覧。"""

    items: list[PreservationRead]
    total: int
    page: int
    page_size: int


class PreservationCleanupRequest(BaseModel):
    """準備の取消は明示操作にし、完成版は取消対象にしない。"""

    model_config = ConfigDict(extra="forbid")
    cancel_preparation: bool = False


class PreservationDownload(BaseModel):
    """認可後に発行する、保存しない短期URL。"""

    url: str
    expires_in: int
