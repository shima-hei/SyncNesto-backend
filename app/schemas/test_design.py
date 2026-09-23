"""テスト設計の入出力契約とグラフ整合性検証。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Name = Annotated[str, Field(min_length=1, max_length=200)]
CellText = Annotated[str, Field(max_length=20000)]


class EntityInput(BaseModel):
    """クライアント生成の安定ID。"""

    model_config = ConfigDict(from_attributes=True, extra="forbid")
    id: UUID
    position: int = Field(default=0, ge=0)


class TestItemInput(EntityInput):
    """テスト項目の内容。"""

    code: str = Field(min_length=1, max_length=100)
    target_feature: CellText = ""
    is_spacer: bool = False
    pattern_table_id: UUID | None = None
    viewpoint: CellText = ""
    content: CellText = ""
    preconditions: CellText = ""
    test_data: CellText = ""
    steps: CellText = ""
    expected_result: CellText = ""
    notes: CellText = ""
    custom_values: dict[str, CellText] = Field(default_factory=dict, max_length=100)


class FactorInput(EntityInput):
    """因子。"""

    name: Name
    table_id: UUID | None = None


class FactorLevelInput(EntityInput):
    """水準。"""

    factor_id: UUID
    name: Name


class TestPatternInput(EntityInput):
    """パターンの識別情報と説明。"""

    code: str = Field(min_length=1, max_length=100)
    description: CellText = ""
    notes: CellText = ""
    enabled: bool = True
    table_id: UUID | None = None


class TestPatternValueInput(EntityInput):
    """パターンの選択水準。"""

    pattern_id: UUID
    factor_id: UUID
    level_id: UUID | None = None


class ExpectedValueInput(EntityInput):
    """期待値の候補。"""

    name: Annotated[str, Field(min_length=1, max_length=20000)]
    table_id: UUID | None = None


class PatternTableInput(EntityInput):
    """独立したパターン表。"""

    name: Name


class TestPatternExpectedValueInput(EntityInput):
    """パターンの期待値選択。"""

    pattern_id: UUID
    expected_value_id: UUID


class TestItemPatternInput(EntityInput):
    """多対多の紐付け。"""

    item_id: UUID
    pattern_id: UUID


class TestDesignColumnInput(EntityInput):
    """ユーザー定義の列。"""

    key: str = Field(pattern=r"^custom_[a-zA-Z0-9_-]{1,80}$")
    label: Name


class CellStyle(BaseModel):
    """許可された書式のみを保存する。"""

    model_config = ConfigDict(extra="forbid")
    bold: bool = False
    align: Literal["left", "center", "right"] = "left"
    background: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class DesignLayout(BaseModel):
    """行IDと列キーに紐づく表示情報。"""

    model_config = ConfigDict(extra="forbid")
    cells: dict[str, CellStyle] = Field(default_factory=dict, max_length=200000)
    widths: dict[str, Annotated[int, Field(ge=60, le=1200)]] = Field(
        default_factory=dict, max_length=500
    )
    heights: dict[str, Annotated[int, Field(ge=28, le=300)]] = Field(
        default_factory=dict, max_length=20000
    )


class TestDesignCreate(BaseModel):
    """設計書作成。"""

    name: Name
    description: CellText = ""


class TestDesignUpdate(TestDesignCreate):
    """設計書の原子的な保存単位。"""

    version: int = Field(ge=1)
    pattern_tables: list[PatternTableInput] = Field(
        default_factory=list, max_length=1000
    )
    items: list[TestItemInput] = Field(max_length=10000)
    factors: list[FactorInput] = Field(max_length=100)
    levels: list[FactorLevelInput] = Field(max_length=10000)
    patterns: list[TestPatternInput] = Field(max_length=10000)
    values: list[TestPatternValueInput] = Field(max_length=100000)
    links: list[TestItemPatternInput] = Field(max_length=20000)
    columns: list[TestDesignColumnInput] = Field(max_length=100)
    layout: DesignLayout
    expected_values: list[ExpectedValueInput] = Field(
        default_factory=list, max_length=10000
    )
    expected_selections: list[TestPatternExpectedValueInput] = Field(
        default_factory=list, max_length=10000
    )

    @model_validator(mode="after")
    def validate_graph(self) -> "TestDesignUpdate":
        """存在しない参照、重複、別因子の水準を拒否する。"""
        for group in (
            self.items,
            self.pattern_tables,
            self.factors,
            self.levels,
            self.patterns,
            self.values,
            self.links,
            self.columns,
            self.expected_values,
            self.expected_selections,
        ):
            if len({row.id for row in group}) != len(group):
                raise ValueError("IDが重複しています")
        for keys in (
            [row.code for row in self.items],
            [(row.table_id, row.code) for row in self.patterns],
            [row.key for row in self.columns],
            [(row.table_id, row.name) for row in self.factors],
            [row.name for row in self.pattern_tables],
            [(row.factor_id, row.name) for row in self.levels],
            [(row.pattern_id, row.factor_id) for row in self.values],
            [(row.item_id, row.pattern_id) for row in self.links],
            [
                (row.pattern_id, row.expected_value_id)
                for row in self.expected_selections
            ],
            [(row.table_id, row.name) for row in self.expected_values],
        ):
            if len(set(keys)) != len(keys):
                raise ValueError("コード・列キー・組み合わせが重複しています")
        factors = {row.id for row in self.factors}
        levels = {row.id: row.factor_id for row in self.levels}
        patterns = {row.id for row in self.patterns}
        items = {row.id for row in self.items}
        expected = {row.id for row in self.expected_values}
        tables = {row.id for row in self.pattern_tables}
        if tables and self.links:
            raise ValueError(
                "個別の組み合わせではなくパターン表を項目に設定してください"
            )
        counts: dict[UUID, int] = {}
        for pattern in self.patterns:
            if pattern.table_id and pattern.enabled:
                counts[pattern.table_id] = counts.get(pattern.table_id, 0) + 1
        if (
            sum(
                counts.get(item.pattern_table_id, 0) if item.pattern_table_id else 1
                for item in self.items
                if not item.is_spacer
            )
            > 20000
        ):
            raise ValueError("展開後のケース数は20,000件までです")
        for row in [*self.factors, *self.patterns, *self.expected_values]:
            if row.table_id is not None and row.table_id not in tables:
                raise ValueError("所属パターン表が存在しません")
            if tables and row.table_id is None:
                raise ValueError("因子・組み合わせ・期待値には所属表が必要です")
        if any(
            row.pattern_table_id is not None and row.pattern_table_id not in tables
            for row in self.items
        ):
            raise ValueError("使用パターン表が存在しません")
        if any(
            row.is_spacer and row.pattern_table_id is not None for row in self.items
        ):
            raise ValueError("区切り行にはパターン表を設定できません")
        spacer_ids = {row.id for row in self.items if row.is_spacer}
        if any(row.item_id in spacer_ids for row in self.links):
            raise ValueError("区切り行には組み合わせを設定できません")
        if any(
            row.is_spacer
            and (
                row.target_feature
                or row.viewpoint
                or row.content
                or row.preconditions
                or row.test_data
                or row.steps
                or row.expected_result
                or row.notes
                or any(row.custom_values.values())
            )
            for row in self.items
        ):
            raise ValueError("区切り行にはテスト項目の内容を設定できません")
        factor_tables = {row.id: row.table_id for row in self.factors}
        pattern_tables = {row.id: row.table_id for row in self.patterns}
        expected_tables = {row.id: row.table_id for row in self.expected_values}
        if any(
            pattern_tables.get(row.pattern_id) != factor_tables.get(row.factor_id)
            for row in self.values
        ):
            raise ValueError("別のパターン表の因子は選択できません")
        if any(
            pattern_tables.get(row.pattern_id)
            != expected_tables.get(row.expected_value_id)
            for row in self.expected_selections
        ):
            raise ValueError("別のパターン表の期待値は選択できません")
        if any(
            row.pattern_id not in patterns or row.expected_value_id not in expected
            for row in self.expected_selections
        ):
            raise ValueError("期待値の参照先が存在しません")
        if any(row.factor_id not in factors for row in self.levels):
            raise ValueError("水準の因子が存在しません")
        for row in self.values:
            if row.pattern_id not in patterns or row.factor_id not in factors:
                raise ValueError("パターンまたは因子が存在しません")
            if row.level_id is not None and levels.get(row.level_id) != row.factor_id:
                raise ValueError("水準が指定された因子に属していません")
        if any(
            row.item_id not in items or row.pattern_id not in patterns
            for row in self.links
        ):
            raise ValueError("紐付け先が存在しません")
        column_keys = {row.key for row in self.columns}
        if any(set(row.custom_values) - column_keys for row in self.items):
            raise ValueError("未定義の任意列が含まれています")
        return self


class TestDesignSummary(TestDesignCreate):
    """設計書一覧。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    version: int
    updated_at: datetime
    item_count: int = 0
    expanded_case_count: int = 0


class TestDesignRead(TestDesignUpdate):
    """編集可能な構造化設計書。"""

    id: int
    project_id: int
    updated_at: datetime


class CaseGenerateRequest(BaseModel):
    """保存済み設計から未生成ケースを作る。"""

    version: int = Field(ge=1)


class TestCaseUpdate(BaseModel):
    """ケースごとの結果とメモ。"""

    version: int = Field(ge=1)
    status: Literal["not_run", "passed", "failed", "blocked", "not_applicable"]
    actual_result: CellText = ""
    notes: CellText = ""


class TestCaseRead(TestCaseUpdate):
    """ケースと設計変更の影響。"""

    id: UUID
    design_id: int
    item_pattern_id: UUID | None
    source: dict
    acknowledged_source: dict | None = None
    stale: bool
    active: bool
    executed_by: int | None = None
    executed_by_name: str | None = None
    executed_at: datetime | None = None
