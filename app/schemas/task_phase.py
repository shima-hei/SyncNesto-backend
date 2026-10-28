"""工程所属と重複しない工数読取契約。"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EffortBasis = Literal["own", "rollup", "unreviewed"]
PhaseScope = Literal["unclassified", "shared", "phase"]


class PhaseAssignment(BaseModel):
    """省略と解除を区別して一括更新する工程所属。"""

    model_config = ConfigDict(extra="forbid")
    kind: PhaseScope
    phase_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_assignment(self):
        """工程だけに参照先を許可する。"""
        if (self.kind == "phase") != (self.phase_id is not None):
            raise ValueError(
                "工程所属には工程IDを指定し、未分類・共通には指定しないでください"
            )
        return self


class PhaseCreate(BaseModel):
    """工程の作成値。"""

    name: str = Field(min_length=1, max_length=100)
    sort_order: int = Field(default=0, ge=0)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        """前後空白を除いた名称を検証する。"""
        return value.strip() if isinstance(value, str) else value


class PhaseUpdate(BaseModel):
    """version付き部分更新。"""

    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    sort_order: int | None = Field(default=None, ge=0)
    archived: bool | None = None
    _strip_name = field_validator("name", mode="before")(PhaseCreate.strip_name)

    @model_validator(mode="after")
    def reject_null(self):
        """省略だけを現状保持として扱う。"""
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("工程の更新値にnullは指定できません")
        return self


class PhaseRead(BaseModel):
    """アーカイブ分を含む工程読取。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    name: str
    sort_order: int
    version: int
    archived_at: datetime | None
    task_count: int = 0


class PhaseList(BaseModel):
    """全件と取りこぼしを区別する工程一覧。"""

    items: list[PhaseRead]
    total: int
    page: int
    page_size: int


class PhaseEventRead(BaseModel):
    """履歴に保存した当時の値。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    phase_id: int
    actor_id: int | None
    old_value: dict[str, object] | None
    new_value: dict[str, object]
    created_at: datetime


class PhaseEventList(BaseModel):
    """工程履歴のページ。"""

    items: list[PhaseEventRead]
    total: int
    page: int
    page_size: int = 20


class EffortMetric(BaseModel):
    """確定小計と未入力・未確認の区別。"""

    minutes: int = 0
    missing_count: int = 0
    unreviewed_count: int = 0
    complete: bool = True


class EffortTotals(BaseModel):
    """一意Task集合から求めた見積と実績。"""

    task_count: int = 0
    estimated: EffortMetric = Field(default_factory=EffortMetric)
    actual: EffortMetric = Field(default_factory=EffortMetric)


class EffortFilters(BaseModel):
    """一覧と同じ条件でページをまたいで集計する。"""

    q: str | None = None
    status: str | None = None
    task_type: str | None = None
    priority: str | None = None
    assignee_id: int | None = None
    requirement_id: int | None = None
    parent_task_id: int | None = None
    root_only: bool | None = None
    start_date_from: date | None = None
    due_date_to: date | None = None
    overdue: bool | None = None
    tag: str | None = None


class PhaseEffortGroup(BaseModel):
    """未分類・共通・アーカイブも独立した集計先にする。"""

    kind: PhaseScope
    phase: PhaseRead | None = None
    totals: EffortTotals


class StatusEffortGroup(BaseModel):
    """完了・中止も同じ対象集合の内訳として保持する。"""

    status: str
    totals: EffortTotals


class EffortSummary(BaseModel):
    """描画行や取得ページ数に左右されないProject集計。"""

    project_id: int
    unit: Literal["minutes"] = "minutes"
    filters: EffortFilters
    totals: EffortTotals
    groups: list[PhaseEffortGroup]
    status_groups: list[StatusEffortGroup]
