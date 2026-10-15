"""API・画面の項目型と設計レビュー契約。"""

import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class SpecificationField(BaseModel):
    """順番を変えても指摘先が変わらない入出力項目。"""

    model_config = ConfigDict(extra="forbid")
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(default="", max_length=200)
    data_type: str = Field(default="string", max_length=100)
    required: bool = False
    description: str = Field(default="", max_length=5000)
    example: str = Field(default="", max_length=5000)
    validation: str = Field(default="", max_length=5000)
    location: Literal["path", "query", "header", "body", "display"] = "body"


class SpecificationRule(BaseModel):
    """レスポンス、画面操作・遷移、状態を説明する行。"""

    model_config = ConfigDict(extra="forbid")
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=5000)
    destination: str = Field(default="", max_length=1000)


class ApiSpecification(BaseModel):
    """設計段階のAPI契約。外部へのAPI実行は行わない。"""

    model_config = ConfigDict(extra="forbid")
    type: Literal["api"] = "api"
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] = "GET"
    path: str = Field(default="", max_length=1000)
    authentication: str = Field(default="", max_length=5000)
    authorization: str = Field(default="", max_length=5000)
    request_fields: list[SpecificationField] = Field(
        default_factory=list, max_length=200
    )
    response_fields: list[SpecificationField] = Field(
        default_factory=list, max_length=200
    )
    responses: list[SpecificationRule] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=20000)


class ScreenSpecification(BaseModel):
    """画面項目、振る舞い、遷移、例外時の表示。"""

    model_config = ConfigDict(extra="forbid")
    type: Literal["screen"] = "screen"
    route: str = Field(default="", max_length=1000)
    audience: str = Field(default="", max_length=5000)
    reference_url: HttpUrl | None = None
    fields: list[SpecificationField] = Field(default_factory=list, max_length=200)
    actions: list[SpecificationRule] = Field(default_factory=list, max_length=100)
    states: list[SpecificationRule] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=20000)


SpecificationContent = Annotated[
    ApiSpecification | ScreenSpecification, Field(discriminator="type")
]
SpecificationStatus = Literal["draft", "in_review", "changes_requested", "approved"]
SpecificationTarget = Literal[
    "requirement", "task", "test_item", "specification", "document"
]


class SpecificationCreate(BaseModel):
    """名前だけから下書きを開始できる構造化文書。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=10000)
    content: SpecificationContent

    @model_validator(mode="after")
    def unique_item_ids(self):
        """同じ指摘先UUIDを複数の行に割り当てない。"""
        ids = [
            item.id
            for value in self.content.__dict__.values()
            if isinstance(value, list)
            for item in value
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("項目IDが重複しています")
        if (
            len(
                json.dumps(
                    self.content.model_dump(mode="json"), ensure_ascii=False
                ).encode("utf-8")
            )
            > 500_000
        ):
            raise ValueError("設計書の本文は500KB以内にしてください")
        return self


class SpecificationUpdate(SpecificationCreate):
    """読み込んだ版で本文を更新する。"""

    version: int = Field(ge=1)


class SpecificationRead(SpecificationCreate):
    """現在の設計書。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    kind: Literal["api", "screen"]
    status: SpecificationStatus
    version: int
    created_by: int
    updated_by: int
    updated_at: datetime


class SpecificationListItem(BaseModel):
    """本文をロードしない一覧項目。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: Literal["api", "screen"]
    title: str
    summary: str
    status: SpecificationStatus
    version: int
    updated_at: datetime


class SpecificationList(BaseModel):
    items: list[SpecificationListItem]
    total: int
    page: int
    page_size: int


class SpecificationStatusUpdate(BaseModel):
    version: int = Field(ge=1)
    status: SpecificationStatus


class SpecificationCommentCreate(BaseModel):
    specification_version: int = Field(ge=1)
    field_path: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=5000)


class SpecificationCommentUpdate(BaseModel):
    version: int = Field(ge=1)
    is_resolved: bool


class SpecificationCommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    author_id: int
    author_name: str = "メンバー"
    field_path: str
    field_snapshot: str
    specification_version: int
    body: str
    is_resolved: bool
    version: int
    created_at: datetime
    anchor_changed: bool = False


class SpecificationRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    number: int
    snapshot: dict
    created_by: int
    created_at: datetime


class SpecificationRevisionSummary(BaseModel):
    """一覧では本文を返さない。"""

    model_config = ConfigDict(from_attributes=True)
    number: int
    created_by: int
    created_at: datetime


class SpecificationLinkCreate(BaseModel):
    target_type: SpecificationTarget
    target_id: str = Field(min_length=1, max_length=50)


class SpecificationLinkRead(SpecificationLinkCreate):
    id: int | None = None
    title: str
    container_id: int | None = None
