"""公開準備の内部契約。公開DTOと内部対応情報を分離する。"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.artifact_release import SourceKind
from app.schemas.test_design import TestDesignUpdate

PUBLIC_PROJECTION = "public-preview-v1"
PublicProjection = Literal["public-preview-v1"]
PublicationState = Literal["prepared", "published", "withdrawn"]
PublicationKind = Literal[
    "requirement_document",
    "section",
    "requirement",
    "requirement_detail",
    "specification",
    "test_design",
    "test_design_part",
]


class PublicationPreviewRequest(BaseModel):
    """既存の封印済み固定版とready保全構成だけを指定する。"""

    model_config = ConfigDict(extra="forbid")
    release_id: UUID
    preservation_id: UUID | None = None
    projection_version: PublicProjection = PUBLIC_PROJECTION
    request_id: UUID


class PublicationCreateRequest(PublicationPreviewRequest):
    """プレビューで確認した公開本文と内部対応のdigestを指定する。"""

    expected_public_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_source_manifest_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class PublicFile(BaseModel):
    """公開して確認する添付の内容識別。保存先や元資料IDは含めない。"""

    model_config = ConfigDict(extra="forbid")
    id: UUID
    filename: str
    content_type: str
    byte_size: int = Field(gt=0)
    content_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class PublicTestDesignGraph(TestDesignUpdate):
    """内部design/project IDを持たず、本文とUUID参照を維持するgraph。"""

    model_config = ConfigDict(extra="forbid")


class PublicationContentMetadata(BaseModel):
    """相手に見せる内容のmetadata。内部認可情報を含まない。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: UUID
    title: str
    schema_version: int
    projection_version: PublicProjection
    public_digest: str
    node_count: int
    byte_size: int
    captured_at: datetime
    files: list[PublicFile]


class PublicationSummary(BaseModel):
    """社内一覧専用。公開状態と顧客承認状態を混同しない。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    projection_version: PublicProjection
    public_digest: str
    source_kinds: list[SourceKind]
    node_count: int
    byte_size: int
    captured_at: datetime
    created_at: datetime
    state: PublicationState = "prepared"
    publication_version: int = 1


class PublicationInternalOrigin(BaseModel):
    """社内だけが読める固定版・保全との対応。公開DTOと分離する。"""

    release_id: UUID
    preservation_id: UUID | None
    source_manifest_digest: str
    body_digest: str


class PublicationRead(BaseModel):
    """社内準備版の詳細。相手向け本文はcontentだけで完結する。"""

    state: PublicationState = "prepared"
    publication_version: int = 1
    content: PublicationContentMetadata
    internal_origin: PublicationInternalOrigin


class PublicationPreview(BaseModel):
    """保存前の要約。全文確認は保存後の分割取得で行う。"""

    title: str
    projection_version: PublicProjection
    public_digest: str
    source_manifest_digest: str
    node_count: int
    node_counts: dict[str, int]
    byte_size: int
    files: list[PublicFile]
    excluded_metadata: list[str]
    state: Literal["not_saved"] = "not_saved"


class PublicationNodeRead(BaseModel):
    """公開表現だけを持つ本文の一部分。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    parent_id: UUID | None
    ordinal: int
    position: int
    node_kind: PublicationKind
    source_version: int | None
    payload: dict[str, Any]
    content_digest: str


class PublicationNodePage(BaseModel):
    """公開用本文の順序付きページ。"""

    publication_id: UUID
    projection_version: PublicProjection
    items: list[PublicationNodeRead]
    total: int
    next_cursor: str | None


class PublicationList(BaseModel):
    """全種類の現在readに基づく社内一覧と件数。"""

    items: list[PublicationSummary]
    total: int
    page: int
    page_size: int
