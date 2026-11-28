"""設計のグループ・図・概要をやり取りする契約。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.design_content import ArtifactKind, json_size, validate_document_tree
from app.schemas.specification import SpecificationListItem

CollectionKind = Literal["group", "api_set", "db_model"]
OpenApiComponentKind = Literal[
    "schemas",
    "parameters",
    "responses",
    "requestBodies",
    "headers",
    "securitySchemes",
    "examples",
    "callbacks",
    "links",
    "pathItems",
]


class CollectionInput(BaseModel):
    """グループ・仕様セット・DBモデルの設定。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: CollectionKind
    name: str = Field(min_length=1, max_length=200)
    parent_id: UUID | None = None
    configuration: dict = Field(default_factory=dict)
    version: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_configuration(self):
        """仕様セットの定義と入れ子サイズを検証する。"""
        validate_document_tree(self.configuration)
        if json_size(self.configuration) > 200_000:
            raise ValueError("設定は200KB以内にしてください")
        if self.kind != "group" and self.parent_id is not None:
            raise ValueError("親グループを指定できるのは機能グループだけです")
        return self


class CollectionRead(CollectionInput):
    """版付きの設計コレクション。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID


class OpenApiUsageItem(BaseModel):
    """共通コンポーネントを使用中の設計の概要。"""

    id: int
    title: str
    path: str
    method: str | None
    version: int


class OpenApiUsageRead(BaseModel):
    """使用中のAPIをセット内からページ取得する。"""

    items: list[OpenApiUsageItem]
    total: int
    page: int
    page_size: int


class MembershipUpdate(BaseModel):
    """案件内の機能グループへの所属を置換する。"""

    version: int = Field(ge=1)
    group_ids: set[UUID] = Field(max_length=40)


class PlacementInput(BaseModel):
    """図ごとの座標または配置解除。"""

    model_config = ConfigDict(extra="forbid")
    specification_id: int = Field(ge=1)
    x: int = Field(ge=0, le=100000)
    y: int = Field(ge=0, le=100000)
    hidden: bool = False
    version: int = Field(default=0, ge=0)


class PlacementRead(PlacementInput):
    """保存済みの図の配置。"""

    model_config = ConfigDict(from_attributes=True)


class PlacementUpdate(BaseModel):
    """複数ノードを同一トランザクションで移動する。"""

    group_id: UUID | None = None
    placements: list[PlacementInput] = Field(min_length=1, max_length=100)


class DesignNode(SpecificationListItem):
    """詳細文書を含めずに図・一覧を表示する。"""

    identifier: str = ""
    subtype: str = ""
    collection_id: UUID | None = None
    group_ids: list[UUID] = Field(default_factory=list)
    placement: PlacementRead | None = None
    field_labels: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class DesignEdge(BaseModel):
    """遷移・外部キー・論理関連から導出した接続線。"""

    id: UUID
    source_id: int
    target_id: int
    name: str
    kind: Literal["transition", "foreign_key", "logical"]
    condition: str = ""


class DesignDiagram(BaseModel):
    """図と一覧の共通プロジェクション。"""

    kind: ArtifactKind
    nodes: list[DesignNode]
    edges: list[DesignEdge]
    total: int
    reference_ids: list[int] = Field(default_factory=list)
    has_more_references: bool = False


class DesignCount(BaseModel):
    """種類と状態の組み合わせごとの件数。"""

    kind: ArtifactKind
    status: str
    count: int


class DesignOverview(BaseModel):
    """最近の変更と対応対象を返す。"""

    counts: list[DesignCount]
    recent: list[SpecificationListItem]
    pending: list[SpecificationListItem]
    unresolved_reviews: list[SpecificationListItem] = Field(default_factory=list)
    impact_candidates: list[SpecificationListItem] = Field(default_factory=list)
    groups: list[CollectionRead]
    group_counts: dict[str, dict[str, int]]
    linked_requirements: int


class OpenApiImport(BaseModel):
    """OpenAPI差分の取得と選択した操作の反映。"""

    collection_id: UUID
    collection_version: int = Field(ge=1)
    document: dict
    selections: dict[str, int] | None = None


class OpenApiChange(BaseModel):
    """既存の設計IDと版を対応付けた取込候補。"""

    key: str
    action: Literal["add", "edit", "same", "retain"]
    title: str
    specification_id: int | None = None
    version: int = 0


class OpenApiResult(BaseModel):
    """取込差分と反映件数。"""

    changes: list[OpenApiChange]
    applied: int = 0


class FormatExport(BaseModel):
    """設計出力と検証結果。"""

    document: dict | None = None
    sql: str = ""
    warnings: list[str] = Field(default_factory=list)


class MockupPreviewRead(BaseModel):
    """許可リストを通過した隔離プレビュー文書。"""

    document: str
    css_blocked: bool


class DesignImpactItem(BaseModel):
    """確認する設計・タスク・要件・テストと変更の理由。"""

    key: str
    level: Literal["error", "warning", "review"]
    title: str
    target_type: str
    target_id: str
    container_id: int | None = None
    target_version: int | None = None
    detail: str
    status: Literal["pending", "confirmed", "needs_action"] = "pending"


class DesignImpactRead(BaseModel):
    """比較元と現在版を明示した変更影響。"""

    source_version: int
    base_version: int | None
    items: list[DesignImpactItem]


class DesignImpactUpdate(BaseModel):
    """最新の設計版に紐付けて確認結果を保存する。"""

    source_version: int = Field(ge=1)
    key: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["confirmed", "needs_action"]
