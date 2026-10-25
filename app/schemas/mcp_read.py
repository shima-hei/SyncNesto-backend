"""開発用MCPの上限付き読取契約。既存28操作のschemaから分離する。"""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.mcp_tools import ToolInput
from app.schemas.search import SearchKind, SearchQuery

ReadState = Literal["loaded", "not_requested", "unavailable", "disabled", "unsupported"]
RelationKind = Literal["designs", "tasks", "tests", "documents"]


class ProjectSearchInput(ToolInput):
    """既存検索と同じ8種を検索する。本文全文や履歴は返さない。"""

    query: SearchQuery = Field(pattern=r"^[^\x00]*$")
    entity_types: list[SearchKind] | None = Field(
        default=None, min_length=1, max_length=8
    )
    status: str | None = Field(
        default=None, min_length=1, max_length=50, pattern=r"^[^\x00]*$"
    )
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=50)


class RequirementContextInput(ToolInput):
    """現在の要件と正式関連を読む。全件/過去の固定版は返さない。"""

    requirement_id: int = Field(gt=0)
    include: list[RelationKind] = Field(
        default_factory=lambda: ["designs", "tasks", "tests", "documents"],
        max_length=4,
    )
    detail_level: Literal["summary", "standard", "full"] = "standard"
    relation_page: int = Field(default=1, ge=1, le=10000)
    relation_page_size: int = Field(default=10, ge=1, le=20)


class ProjectBriefInput(ToolInput):
    """基本情報と指定した集計だけを取得する。現在工程は推測しない。"""

    include: list[Literal["objectives", "tasks", "milestones"]] = Field(
        default_factory=lambda: ["objectives", "tasks", "milestones"], max_length=3
    )
    limit: int = Field(default=5, ge=1, le=20)
    as_of_date: date | None = Field(
        default=None, description="期限超過の比較日。省略時はUTC当日。"
    )


class ReadReference(BaseModel):
    """実在する追加読取ツールと識別引数。ツールがない対象はnullとする。"""

    tool: str
    arguments: dict[str, Any]


class ReadEntity(BaseModel):
    """種別の異なる数値/UUIDのIDを文字列へ正規化する。"""

    entity_type: str
    id: str
    project_id: int
    code: str | None = None
    title: str
    status: str | None = None
    version: int | None = None
    updated_at: datetime | None = None
    container_id: int | None = None
    container_version: int | None = None
    target_date: date | None = None
    read: ReadReference | None = None


class ReadPage(BaseModel):
    """live読取のため、複数ページをまたぐsnapshotの保証はしない。"""

    page: int
    page_size: int
    total: int
    has_more: bool
    next_page: int | None
    consistency: Literal["live"] = "live"


class ReadCollection(BaseModel):
    """未取得/権限外では件数とhas_moreを返さない。"""

    state: ReadState
    items: list[ReadEntity] = Field(default_factory=list)
    pagination: ReadPage | None = None
    read: ReadReference | None = None

    @model_validator(mode="after")
    def validate_unloaded(self):
        """取得前/権限外の関連へデータや件数を混入させない。"""
        if self.state != "loaded" and (self.items or self.pagination is not None):
            raise ValueError("未取得の関連へ結果や件数を設定できません")
        return self


class SearchMatch(ReadEntity):
    """本文の一致箇所は240文字以内。"""

    excerpt: str = Field(max_length=240)


class ProjectSearchRead(BaseModel):
    """summary専用の検索結果。"""

    project_id: int
    items: list[SearchMatch]
    pagination: ReadPage
    warnings: list[str] = Field(default_factory=list)


class RequirementContent(BaseModel):
    """summary時は本文未取得。nullの本文と未取得をstateで区別する。"""

    state: ReadState
    description: str | None = None
    acceptance_criteria: str | None = None
    rationale: str | None = None
    truncated_fields: list[str] = Field(default_factory=list)


class RequirementContextRead(BaseModel):
    """現在値の文脈であり、正式承認や要件合格を再計算しない。"""

    entity: ReadEntity
    document: ReadEntity
    section: ReadEntity | None
    content: RequirementContent
    relations: dict[RelationKind, ReadCollection]
    review: ReadCollection
    consistency: Literal["live"] = "live"
    warnings: list[str] = Field(default_factory=list)


class ObjectiveContext(BaseModel):
    """選択されている正本だけを参照し、説明から自動補完しない。"""

    state: ReadState
    source_state: Literal["selected", "unselected", "unavailable"] | None = None
    source_version: int | None = None
    document: ReadEntity | None = None
    goal: str | None = None
    scope: str | None = None
    completion_criteria: str | None = None
    truncated_fields: list[str] = Field(default_factory=list)


class TaskCounts(BaseModel):
    """親と子を含む有効Task行の保存状態を数える。"""

    state: ReadState
    total: int | None = None
    by_status: dict[str, int] | None = None
    statuses_truncated: bool = False
    overdue: int | None = None
    as_of_date: date | None = None
    basis: str | None = None


class ProjectBriefRead(BaseModel):
    """全タスク本文を展開せず、出典と集計条件を返す。"""

    entity: ReadEntity
    description: str | None = None
    description_truncated: bool = False
    objectives: ObjectiveContext
    tasks: TaskCounts
    milestones: ReadCollection
    current_phase: ReadState = "unsupported"
    review_and_approvals: ReadState = "not_requested"
    warnings: list[str] = Field(default_factory=list)
