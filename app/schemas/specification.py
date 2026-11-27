"""API・画面の項目型と設計レビュー契約。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from app.schemas.comment_image import CommentImageCreateFields, CommentImageReadFields
from app.schemas.design_content import (
    ArtifactKind,
    DatabaseSpecification,
    DesignMapping,
    HtmlMockup,
    ProcessingStep,
    ScreenKind,
    ScreenTransition,
    WireframeArtboard,
    ensure_unique_design_ids,
    json_size,
)


class SpecificationField(BaseModel):
    """順番を変えても指摘先が変わらない入出力項目。"""

    model_config = ConfigDict(extra="forbid")
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(default="", max_length=200)
    logical_id: str = Field(default="", max_length=100)
    ui_component: str = Field(default="", max_length=100)
    default_value: str = Field(default="", max_length=1000)
    constraints: str = Field(default="", max_length=3000)
    visibility: str = Field(default="", max_length=1000)
    enabled_when: str = Field(default="", max_length=1000)
    error_message: str = Field(default="", max_length=1000)
    data_type: str = Field(default="string", max_length=100)
    required: bool = False
    description: str = Field(default="", max_length=5000)
    example: str = Field(default="", max_length=5000)
    validation: str = Field(default="", max_length=5000)
    location: Literal[
        "path", "query", "header", "cookie", "body", "display", "action"
    ] = "body"


class SpecificationRule(BaseModel):
    """レスポンス、画面操作・遷移、状態を説明する行。"""

    model_config = ConfigDict(extra="forbid")
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=5000)
    destination: str = Field(default="", max_length=1000)


class ScreenEvent(SpecificationRule):
    """画面イベントの処理と分岐。"""

    trigger: str = Field(default="", max_length=200)
    steps: list[ProcessingStep] = Field(default_factory=list, max_length=100)


class ScreenState(SpecificationRule):
    """設計対象システムの画面状態と利用者区分。"""

    audience: str = Field(default="", max_length=200)
    message: str = Field(default="", max_length=1000)
    field_id: UUID | None = None
    display: Literal["unchanged", "visible", "hidden"] = "unchanged"
    access: Literal["unchanged", "enabled", "disabled", "read_only"] = "unchanged"
    value: str = Field(default="", max_length=1000)


class ApiSpecification(BaseModel):
    """設計段階のAPI契約。外部へのAPI実行は行わない。"""

    model_config = ConfigDict(extra="forbid")
    type: Literal["api"] = "api"
    collection_id: UUID | None = None
    operation: dict | None = None
    mappings: list[DesignMapping] = Field(default_factory=list, max_length=100)
    processing_steps: list[ProcessingStep] = Field(default_factory=list, max_length=100)
    method: (
        Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"]
        | None
    ) = "GET"
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
    screen_kind: ScreenKind = "other"
    transitions: list[ScreenTransition] = Field(default_factory=list, max_length=150)
    artboards: list[WireframeArtboard] = Field(default_factory=list, max_length=20)
    mappings: list[DesignMapping] = Field(default_factory=list, max_length=100)
    mockups: list[HtmlMockup] = Field(default_factory=list, max_length=15)
    route: str = Field(default="", max_length=1000)
    audience: str = Field(default="", max_length=5000)
    reference_url: HttpUrl | None = None
    fields: list[SpecificationField] = Field(default_factory=list, max_length=200)
    actions: list[ScreenEvent] = Field(default_factory=list, max_length=100)
    states: list[ScreenState] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=20000)

    @model_validator(mode="after")
    def validate_item_references(self):
        """画面内で消失した項目への参照を検出する。"""
        field_ids = {item.id for item in self.fields}
        event_ids = {item.id for item in self.actions}
        state_ids = {item.id for item in self.states}
        for transition in self.transitions:
            if transition.event_id is not None and transition.event_id not in event_ids:
                raise ValueError("遷移のイベントが見つかりません")
        for state in self.states:
            if state.field_id is not None and state.field_id not in field_ids:
                raise ValueError("状態の対象項目が見つかりません")
        for board in self.artboards:
            if board.state_id is not None and board.state_id not in state_ids:
                raise ValueError("アートボードの状態が見つかりません")
            for item in board.components:
                if item.field_id is not None and item.field_id not in field_ids:
                    raise ValueError("ワイヤーフレームの画面項目が見つかりません")
                if item.event_id is not None and item.event_id not in event_ids:
                    raise ValueError("ワイヤーフレームのイベントが見つかりません")
        return self


SpecificationContent = Annotated[
    ApiSpecification | ScreenSpecification | DatabaseSpecification,
    Field(discriminator="type"),
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
        content = self.content.model_dump(mode="json")
        ensure_unique_design_ids(content)
        if json_size(content) > 500_000:
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
    kind: ArtifactKind
    status: SpecificationStatus
    version: int
    created_by: int
    updated_by: int
    updated_at: datetime


class SpecificationListItem(BaseModel):
    """本文をロードしない一覧項目。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: ArtifactKind
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


class SpecificationCommentCreate(CommentImageCreateFields):
    specification_version: int = Field(ge=1)
    field_path: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=5000)


class SpecificationCommentUpdate(BaseModel):
    version: int = Field(ge=1)
    is_resolved: bool


class SpecificationCommentRead(CommentImageReadFields):
    model_config = ConfigDict(from_attributes=True)
    id: int
    author_id: int
    author_name: str = "メンバー"
    author_avatar_url: str | None = None
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
