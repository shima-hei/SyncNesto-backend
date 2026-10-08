"""MCPの小さな操作単位。通常APIの入力を再利用する。"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.requirement import (
    RequirementCreate,
    RequirementDocumentCreate,
    RequirementSectionCreate,
)
from app.schemas.task import (
    MilestoneCreate,
    TaskCreate,
    TaskDependencyCreate,
    TaskUpdate,
)
from app.schemas.test_collaboration import (
    RequirementTestItemCreate,
    TestDesignCommentCreate,
)
from app.schemas.test_design import (
    ExpectedValueInput,
    FactorInput,
    FactorLevelInput,
    PatternTableInput,
    TestDesignCreate,
    TestItemInput,
    TestItemPatternInput,
    TestPatternExpectedValueInput,
    TestPatternInput,
    TestPatternValueInput,
)


class EmptyInput(BaseModel):
    """引数なしの参照ツール。"""

    model_config = ConfigDict(extra="forbid")


class ToolInput(BaseModel):
    """接続で許可されたProjectを明示する。"""

    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(gt=0)


class WriteInput(ToolInput):
    """再送時には同じキーと入力を使う。"""

    idempotency_key: str = Field(
        min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"
    )


class PageInput(ToolInput):
    """大量取得を避けるページ指定。"""

    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=30, ge=1, le=100)


class RequirementListInput(PageInput):
    """要件定義書内の要件を読む。"""

    document_id: int = Field(gt=0)


class RequirementGetInput(ToolInput):
    """対象種別と安定IDを指定する。"""

    target_type: Literal["document", "section", "requirement_item", "open_issue"]
    target_id: int = Field(gt=0)


class DocumentCreateInput(WriteInput):
    """下書き文書を作成する。"""

    data: RequirementDocumentCreate


class RequirementCommentsInput(RequirementGetInput):
    """既存の指摘を分割して取得する。"""

    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=30, ge=1, le=100)


class SectionCreateInput(WriteInput):
    """文書内にセクションを作成する。"""

    document_id: int = Field(gt=0)
    data: RequirementSectionCreate


class RequirementCreateInput(WriteInput):
    """要件を下書きで作成する。"""

    data: RequirementCreate


class RequirementCommentInput(WriteInput):
    """一件の指摘を対象のフィールド・引用箇所へ投稿する。"""

    target_type: Literal["document", "section", "requirement_item", "open_issue"]
    target_id: int = Field(gt=0)
    version: int = Field(ge=1)
    field: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=1, max_length=2000)
    quote_start: int = Field(ge=0, description="Unicode code point単位の開始位置")
    body: str = Field(min_length=1, max_length=20000)


class DesignGetInput(PageInput):
    """設計の必要な構成要素だけをページ取得する。"""

    design_id: int = Field(gt=0)
    component: Literal[
        "items",
        "pattern_tables",
        "factors",
        "levels",
        "patterns",
        "values",
        "expected_values",
        "expected_selections",
        "links",
    ] = "items"


class DesignCreateInput(WriteInput):
    """設計書を作成する。"""

    data: TestDesignCreate


class DesignAppendInput(WriteInput):
    """既存グラフを保持して追加する。各IDは新規UUIDを指定する。"""

    design_id: int = Field(gt=0)
    version: int = Field(ge=1)
    items: list[TestItemInput] = Field(default_factory=list, max_length=100)
    pattern_tables: list[PatternTableInput] = Field(default_factory=list, max_length=20)
    factors: list[FactorInput] = Field(default_factory=list, max_length=100)
    levels: list[FactorLevelInput] = Field(default_factory=list, max_length=100)
    patterns: list[TestPatternInput] = Field(default_factory=list, max_length=100)
    values: list[TestPatternValueInput] = Field(default_factory=list, max_length=100)
    expected_values: list[ExpectedValueInput] = Field(
        default_factory=list, max_length=100
    )
    expected_selections: list[TestPatternExpectedValueInput] = Field(
        default_factory=list, max_length=100
    )
    links: list[TestItemPatternInput] = Field(default_factory=list, max_length=100)


class DesignCommentInput(WriteInput):
    """一件の指摘。行番号ではなくUUIDとフィールドで指定する。"""

    design_id: int = Field(gt=0)
    version: int = Field(ge=1)
    data: TestDesignCommentCreate


class DesignRequirementLinkInput(WriteInput):
    """テスト項目と要件を紐づける。"""

    design_id: int = Field(gt=0)
    data: RequirementTestItemCreate


class TaskListInput(PageInput):
    """タスクの検索・要件からの絞り込み。"""

    requirement_id: int | None = None
    q: str | None = Field(default=None, max_length=200)


class TaskGetInput(ToolInput):
    """タスクの現在値と版を読む。"""

    task_id: int = Field(gt=0)


class TaskCreateInput(WriteInput):
    """要件・親タスクも指定できるタスク作成。"""

    data: TaskCreate


class TaskDependenciesInput(TaskGetInput):
    """前後関係を分割取得する。"""

    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=30, ge=1, le=100)


class TaskUpdateInput(WriteInput):
    """現在のversionで一件を更新する。"""

    task_id: int = Field(gt=0)
    data: TaskUpdate


class DependencyCreateInput(WriteInput):
    """既存の終了→開始依存関係を追加する。"""

    data: TaskDependencyCreate


class MilestoneCreateInput(WriteInput):
    """Projectのマイルストーンを追加する。"""

    data: MilestoneCreate


class ScheduleChange(BaseModel):
    """確認する日程変更。"""

    model_config = ConfigDict(extra="forbid")
    task_id: int = Field(gt=0)
    version: int = Field(ge=1)
    start_date: date | None
    due_date: date | None


class SchedulePreviewInput(WriteInput):
    """日程変更前後と前提を提示する。業務データは更新しない。"""

    changes: list[ScheduleChange] = Field(min_length=1, max_length=50)
    assumptions: list[str] = Field(min_length=1, max_length=10)


class ScheduleApplyInput(WriteInput):
    """確認済みプレビューを同じ内容・版で適用する。"""

    preview_key: str = Field(min_length=16, max_length=100)
    confirmation_hash: str = Field(min_length=64, max_length=64)


class McpOperation(BaseModel):
    """専用連携APIの封筒。入力の詳細はcatalogが返す。"""

    model_config = ConfigDict(extra="forbid")
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict
