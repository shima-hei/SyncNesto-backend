"""設計ワークスペースで編集・参照する項目。"""

import json
from collections.abc import Sequence
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class DesignItem(BaseModel):
    """表示名や並べ替えに依存しない設計項目。"""

    model_config = ConfigDict(extra="forbid")
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=5000)


ScreenKind = Literal[
    "list",
    "detail",
    "create",
    "edit",
    "dashboard",
    "modal",
    "drawer",
    "layout",
    "other",
]
DatabaseType = Literal[
    "bigint",
    "integer",
    "smallint",
    "numeric",
    "real",
    "double precision",
    "text",
    "varchar",
    "boolean",
    "uuid",
    "date",
    "timestamp",
    "timestamptz",
    "jsonb",
    "bytea",
    "inet",
]
ReferentialAction = Literal[
    "NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"
]
PlacementMode = Literal["free", "vertical", "horizontal", "grid"]
ArtifactKind = Literal["screen", "api", "database"]


class ProcessingStep(DesignItem):
    """イベント・APIで共通の処理仕様。"""

    kind: Literal[
        "validation",
        "condition",
        "api",
        "set_value",
        "message",
        "state",
        "transition",
        "db_read",
        "db_write",
        "auth",
        "transaction",
        "other",
    ] = "other"
    condition: str = Field(default="", max_length=3000)
    on_success: UUID | None = None
    on_failure: UUID | None = None
    specification_id: int | None = Field(default=None, ge=1)


class ScreenTransition(DesignItem):
    """イベントと画面遷移図が読み出す遷移の正本。"""

    target_id: int = Field(ge=1)
    event_id: UUID | None = None
    trigger: str = Field(default="", max_length=200)
    condition: str = Field(default="", max_length=3000)
    mode: Literal["page", "modal", "drawer", "new_tab", "other"] = "page"
    parameters: str = Field(default="", max_length=3000)


class WireframeLayer(DesignItem):
    """ワイヤーフレームと共通部品で使う再帰配置。"""

    kind: Literal[
        "text",
        "heading",
        "button",
        "input",
        "number",
        "textarea",
        "select",
        "checkbox",
        "radio",
        "date",
        "file",
        "table",
        "image",
        "icon",
        "tabs",
        "modal",
        "drawer",
        "menu",
        "pagination",
        "divider",
        "container",
        "chart",
    ] = "text"
    x: int = Field(default=0, ge=0, le=10000)
    y: int = Field(default=0, ge=0, le=10000)
    width: int = Field(default=160, ge=12, le=10000)
    height: int = Field(default=40, ge=12, le=10000)
    hidden: bool = False
    locked: bool = False
    parent_id: UUID | None = None
    layout: PlacementMode = "free"
    gap: int = Field(default=8, ge=0, le=200)
    padding: int = Field(default=8, ge=0, le=200)
    columns: int = Field(default=2, ge=1, le=20)
    column_span: int = Field(default=1, ge=1, le=20)
    table_rows: list[list[str]] = Field(default_factory=list, max_length=30)


class WireframePartPin(BaseModel):
    """配置先の画面に保存する部品の版と、原本から画面内へのID対応。"""

    model_config = ConfigDict(extra="forbid")
    part_id: UUID
    version: int = Field(ge=1, le=2_147_483_647)
    layer_ids: dict[UUID, UUID] = Field(max_length=199)


class WireframeComponent(WireframeLayer):
    """画面固有の項目・イベントと、選択した共通部品の版を保持する。"""

    field_id: UUID | None = None
    event_id: UUID | None = None
    part_pin: WireframePartPin | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_part_pin(self):
        if self.part_pin is not None and self.kind != "container":
            raise ValueError("共通部品はコンテナに配置してください")
        return self


def validate_wireframe_layers(components: Sequence[WireframeLayer]) -> None:
    """各レイヤーが同じアートボードまたは部品に属することを確認する。"""
    parents = {item.id: item.parent_id for item in components}
    containers = {item.id for item in components if item.kind == "container"}
    for item in components:
        seen = {item.id}
        parent = item.parent_id
        while parent is not None:
            if parent in seen or parent not in containers:
                raise ValueError("レイヤーの配置先が無効です")
            seen.add(parent)
            parent = parents[parent]


class WireframeArtboard(DesignItem):
    """サイズ・状態の異なるワイヤーフレーム。"""

    width: int = Field(default=1024, ge=240, le=3840)
    height: int = Field(default=650, ge=240, le=3840)
    layout: PlacementMode = "free"
    state_id: UUID | None = None
    components: list[WireframeComponent] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_layers(self):
        validate_wireframe_layers(self.components)
        return self


class HtmlMockup(DesignItem):
    """表示用HTMLと分離して保存する原本。画像は添付済みの相対識別子を使う。"""

    html: str = Field(default="", max_length=100000)
    css: str = Field(default="", max_length=50000)
    width: int = Field(default=1024, ge=240, le=3840)
    height: int = Field(default=650, ge=240, le=3840)


class DatabaseColumn(DesignItem):
    """論理属性・物理カラム・値の制約。"""

    physical_name: str = Field(default="", max_length=63)
    logical_type: str = Field(default="", max_length=100)
    data_type: DatabaseType = "text"
    length: int | None = Field(default=None, ge=1, le=10485760)
    precision: int | None = Field(default=None, ge=1, le=1000)
    scale: int | None = Field(default=None, ge=0, le=1000)
    nullable: bool = True
    default_value: str | None = Field(default=None, max_length=1000)
    identity: Literal["always", "by_default"] | None = None


class DatabaseConstraint(DesignItem):
    """複合キーにも対応し、物理ER図で参照する外部キーの正本。"""

    kind: Literal["primary_key", "foreign_key", "unique", "check", "not_null"]
    column_ids: list[UUID] = Field(default_factory=list, max_length=32)
    target_id: int | None = Field(default=None, ge=1)
    target_column_ids: list[UUID] = Field(default_factory=list, max_length=32)
    on_delete: ReferentialAction = "NO ACTION"
    on_update: ReferentialAction = "NO ACTION"
    expression: str = Field(default="", max_length=3000)


class DatabaseIndex(DesignItem):
    """順序を持つ複合インデックス。"""

    column_ids: list[UUID] = Field(default_factory=list, max_length=32)
    descending: list[UUID] = Field(default_factory=list, max_length=32)
    unique: bool = False
    method: Literal["btree", "hash", "gin", "gist", "brin", "spgist"] = "btree"
    predicate: str = Field(default="", max_length=3000)


class DatabaseRelationship(DesignItem):
    """物理外部キーから独立した論理リレーション。"""

    target_id: int = Field(ge=1)
    cardinality: Literal["one_to_one", "one_to_many", "many_to_many"] = "one_to_many"
    optional: bool = False
    constraint_id: UUID | None = None


class DesignMapping(DesignItem):
    """設計間の項目を不変ID入りのパスで対応付ける。"""

    source_path: str = Field(min_length=1, max_length=280)
    target_id: int = Field(ge=1)
    target_path: str = Field(min_length=1, max_length=280)
    meaning: Literal["implements", "maps", "calls", "reads", "writes", "verifies"] = (
        "maps"
    )


class DatabaseSpecification(BaseModel):
    """一つのテーブルに論理定義と物理定義を持たせる。"""

    model_config = ConfigDict(extra="forbid")
    type: Literal["database"] = "database"
    collection_id: UUID | None = None
    namespace: str = Field(default="public", max_length=63)
    physical_name: str = Field(default="", max_length=63)
    notes: str = Field(default="", max_length=20000)
    columns: list[DatabaseColumn] = Field(default_factory=list, max_length=250)
    constraints: list[DatabaseConstraint] = Field(default_factory=list, max_length=100)
    indexes: list[DatabaseIndex] = Field(default_factory=list, max_length=100)
    mappings: list[DesignMapping] = Field(default_factory=list, max_length=100)
    relationships: list[DatabaseRelationship] = Field(
        default_factory=list, max_length=100
    )

    @model_validator(mode="after")
    def validate_column_references(self):
        """テーブル内の参照先と制約の形を検証する。"""
        column_ids = {column.id for column in self.columns}
        for item in [*self.constraints, *self.indexes]:
            if (
                len(item.column_ids) != len(set(item.column_ids))
                or not set(item.column_ids) <= column_ids
            ):
                raise ValueError("制約・インデックスのカラム指定が無効です")
        for index in self.indexes:
            if not set(index.descending) <= set(index.column_ids):
                raise ValueError("降順指定はインデックスの対象カラムに限ります")
        constraint_ids = {
            item.id for item in self.constraints if item.kind == "foreign_key"
        }
        for item in self.relationships:
            if (
                item.constraint_id is not None
                and item.constraint_id not in constraint_ids
            ):
                raise ValueError("論理リレーションの外部キー指定が無効です")
        return self


def validate_document_tree(value: JsonValue, *, maximum_depth: int = 35) -> None:
    """外部参照を解決せず、入れ子と構造の複雑さを制限する。"""
    remaining = 20000
    nodes = [(value, 0)]
    while nodes:
        current, depth = nodes.pop()
        remaining -= 1
        if depth > maximum_depth or remaining < 0:
            raise ValueError("定義の階層または要素数の上限を超えています")
        if isinstance(current, dict):
            nodes.extend((node, depth + 1) for node in current.values())
        elif isinstance(current, list):
            nodes.extend((node, depth + 1) for node in current)


def ensure_unique_design_ids(value: JsonValue) -> None:
    """型で管理する項目を対象に、UUIDの衝突を防止する。"""
    ids: set[str] = set()

    def visit(node: JsonValue):
        if isinstance(node, list):
            for entry in node:
                visit(entry)
        elif isinstance(node, dict):
            for key, child in node.items():
                if key == "id" and isinstance(child, str):
                    if child in ids:
                        raise ValueError("項目IDが重複しています")
                    ids.add(child)
                elif key not in {"operation", "sample_override"}:
                    visit(child)

    validate_document_tree(value)
    visit(value)


def json_size(value: JsonValue) -> int:
    """Unicodeの本文サイズをUTF-8バイト数で測る。"""
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))


ShortText = Annotated[str, Field(max_length=500)]
