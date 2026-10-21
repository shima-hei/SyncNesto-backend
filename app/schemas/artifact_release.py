"""固定版の作成・一覧・分割読取契約。顧客公開の契約とは分離する。"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceKind = Literal[
    "requirement", "requirement_document", "specification", "test_design"
]
ProjectionVersion = Literal["internal-preview-v1", "internal-preview-v2"]


class ArtifactSource(BaseModel):
    """サーバー側で内容と所属を確認する元対象。"""

    model_config = ConfigDict(extra="forbid")
    kind: SourceKind
    id: int = Field(gt=0)
    version: int = Field(gt=0)


class ArtifactPreviewRequest(BaseModel):
    """本文ではなく対象と版だけを受け取る。"""

    model_config = ConfigDict(extra="forbid")
    source: ArtifactSource
    included_sources: list[ArtifactSource] = Field(default_factory=list, max_length=100)
    projection_version: ProjectionVersion = "internal-preview-v1"

    @model_validator(mode="after")
    def unique_sources(self):
        """同じ元対象を重複して固定しない。"""
        refs = [(s.kind, s.id) for s in [self.source, *self.included_sources]]
        if len(refs) != len(set(refs)):
            raise ValueError("固定対象が重複しています")
        return self


class ArtifactCreateRequest(ArtifactPreviewRequest):
    """確認済みの構成hashと再送用IDを指定する。"""

    request_id: UUID
    expected_scope_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class ArtifactPreview(BaseModel):
    """previewの対象構成と容量。内部本文をエラーへ載せない。"""

    source: ArtifactSource
    included_sources: list[ArtifactSource]
    projection_version: ProjectionVersion
    title: str
    scope_digest: str
    node_count: int
    node_counts: dict[str, int]
    byte_size: int
    captured_at: datetime


class ArtifactReleaseSummary(BaseModel):
    """現在の本文とは区別する固定版のmetadata。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: int
    source_kind: SourceKind
    source_id: int
    source_version: int
    schema_version: int
    projection_version: ProjectionVersion
    title: str
    content_digest: str
    node_count: int
    byte_size: int
    captured_at: datetime
    created_at: datetime
    created_by: int


class ArtifactReleaseMetadata(ArtifactReleaseSummary):
    """元対象の現在版だけを補足し、本文全体の一致とはみなさない。"""

    current_source_version: int | None
    source_state: Literal["present", "deleted"]
    scope_comparison: Literal["not_checked"] = "not_checked"
    sources: list[ArtifactSource]


class ArtifactReleaseList(BaseModel):
    """許可された種類だけから算出する件数と一覧。"""

    items: list[ArtifactReleaseSummary]
    total: int
    page: int
    page_size: int


class ArtifactNodeRead(BaseModel):
    """固定本文の一部分。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    release_id: UUID
    parent_id: UUID | None
    ordinal: int
    position: int
    node_kind: str
    source_id: int
    source_version: int | None
    payload: dict[str, Any]
    content_digest: str


class ArtifactNodePage(BaseModel):
    """byte数にも上限を持つ不変本文のページ。"""

    release_id: UUID
    items: list[ArtifactNodeRead]
    total: int
    next_cursor: str | None
