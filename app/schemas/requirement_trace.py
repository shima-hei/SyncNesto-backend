"""要件の本文を複製しない、範囲を限定した追跡の読取形式。"""

from typing import Literal

from pydantic import BaseModel, Field


class TraceTarget(BaseModel):
    """専門画面にある正本への参照。"""

    id: str
    code: str
    title: str
    status: str | None = None
    version: int | None = None
    design_id: int | None = None
    context_title: str | None = None
    position: int | None = None


class TraceFacet(BaseModel):
    """権限外を件数0や関連なしへ変換しない。"""

    state: Literal["ready", "restricted"]
    count: int | None = None
    items: list[TraceTarget] = Field(default_factory=list)
    has_more: bool = False


class TraceCapabilities(BaseModel):
    """既存の判断画面を開く入口。判断操作の許可ではない。"""

    read_approvals: bool = False


class RequirementTraceRow(BaseModel):
    """一覧では本文・議論・実行履歴を取得しない。"""

    id: int
    document_id: int
    document_code: str
    document_title: str
    requirement_code: str
    title: str
    version: int
    status: str
    designs: TraceFacet
    tasks: TraceFacet
    test_items: TraceFacet
    agreement_state: Literal["not_evaluated"] = "not_evaluated"
    execution_state: Literal["not_evaluated"] = "not_evaluated"


class RequirementTracePage(BaseModel):
    """案件/本人の読取結果だけをページ化する。"""

    enabled: bool
    state: Literal["disabled", "restricted", "ready"]
    page: int
    page_size: int
    total: int | None = None
    items: list[RequirementTraceRow] = Field(default_factory=list)
    capabilities: TraceCapabilities = Field(default_factory=TraceCapabilities)


class RequirementTraceDetail(RequirementTraceRow):
    """選択した1要件の抜粋と、上限付きの関連。"""

    description: str | None
    description_truncated: bool
    acceptance_criteria: str | None
    acceptance_criteria_truncated: bool
    capabilities: TraceCapabilities
