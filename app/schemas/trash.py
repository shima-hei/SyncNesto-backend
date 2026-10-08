"""論理削除した業務データの概要と復元要求。"""

from datetime import datetime
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field


class TrashKind(StrEnum):
    """ごみ箱で扱う資源。"""

    REQUIREMENT_DOCUMENT = "requirement_document"
    REQUIREMENT_SECTION = "requirement_section"
    REQUIREMENT = "requirement"
    TASK = "task"
    TEST_DESIGN = "test_design"
    DOCUMENT = "document"
    DOCUMENT_ATTACHMENT = "document_attachment"


class TrashItem(BaseModel):
    """本文・ファイルキーを含めない削除概要。"""

    kind: TrashKind
    id: str
    title: str
    container_id: int | None = None
    deleted_at: datetime
    expires_at: datetime | None
    version: int | None
    can_restore: bool
    blocked_reason: str | None = None


class TrashRead(BaseModel):
    """権限内の削除一覧と保持方針。"""

    items: list[TrashItem]
    total: int
    page: int
    page_size: int
    retention_days: int


class TrashRestore(BaseModel):
    """再削除・更新後に古い一覧から復元しないための照合値。"""

    deleted_at: AwareDatetime
    version: int | None = Field(default=None, ge=1)
