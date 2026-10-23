"""確認の準備と実際の要件合否を分離する入出力。"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VerificationQuote(StrictInput):
    field: Literal["whole", "description", "acceptance_criteria"] = "whole"
    start: int | None = Field(default=None, ge=0)
    text: str = Field(default="", max_length=4000)


class VerificationTestTarget(StrictInput):
    item_id: UUID
    pattern_scope: Literal["all_enabled", "selected"] = "all_enabled"
    pattern_ids: list[UUID] = Field(default_factory=list, max_length=1000)


class VerificationRow(StrictInput):
    id: UUID
    reference: VerificationQuote = Field(default_factory=VerificationQuote)
    description: str = Field(default="", max_length=4000)
    method: Literal["test", "manual", "not_applicable"] | None = None
    targets: list[VerificationTestTarget] = Field(default_factory=list, max_length=1000)
    manual_plan: str = Field(default="", max_length=4000)
    not_applicable_reason: str = Field(default="", max_length=4000)
    notes: str = Field(default="", max_length=8000)


class VerificationChecklist(StrictInput):
    rows: list[VerificationRow] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_rows(self):
        if len({row.id for row in self.rows}) != len(self.rows):
            raise ValueError("確認行IDが重複しています")
        if (
            sum(
                len(row.targets) + sum(len(t.pattern_ids) for t in row.targets)
                for row in self.rows
            )
            > 1000
        ):
            raise ValueError("対象参照は合計1000件までです")
        return self


class VerificationCreate(StrictInput):
    source_requirement_id: int = Field(gt=0)
    release_id: UUID
    target_node_id: UUID
    expected_release_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: UUID


class VerificationUpdate(StrictInput):
    version: int = Field(gt=0)
    operation_id: UUID
    checklist: VerificationChecklist
    confirm_coverage: bool = False


class VerificationSummary(BaseModel):
    id: UUID
    source_requirement_id: int
    title: str
    current_version: int
    created_at: datetime
    updated_at: datetime


class VerificationList(BaseModel):
    enabled: bool
    state: Literal["ready", "disabled", "restricted"]
    can_prepare: bool = False
    items: list[VerificationSummary] = Field(default_factory=list)
    next_cursor: str | None = None


class VerificationCoverage(BaseModel):
    subject_digest: str
    rows_digest: str
    actor_id: int
    confirmed_at: datetime


class VerificationSavedChecklist(VerificationChecklist):
    coverage: VerificationCoverage | None = None


class VerificationRevisionRead(BaseModel):
    version: int
    checklist: VerificationSavedChecklist
    checklist_digest: str
    actor_name: str
    created_at: datetime


class VerificationDetail(VerificationSummary):
    subject_digest: str
    fixed_subject: dict[str, Any]
    comparison: Literal["same", "changed", "unavailable"]
    can_prepare: bool
    revision: VerificationRevisionRead


class VerificationRevisionPage(BaseModel):
    items: list[VerificationRevisionRead]
    next_cursor: str | None = None


class VerificationCandidate(BaseModel):
    selected: bool = True
    item_id: UUID
    item_title: str
    design_id: int
    pattern_id: UUID | None = None
    pattern_name: str | None = None
    case_id: UUID | None = None
    state: Literal[
        "inactive",
        "not_generated",
        "not_run",
        "in_progress",
        "passed",
        "failed",
        "blocked",
        "not_applicable",
    ]
    execution_id: UUID | None = None
    executed_at: datetime | None = None
    comparison: Literal["same", "changed", "not_evaluated"]
    reason: str | None = None
    requirement_version_recorded: Literal[False] = False


class VerificationCandidatePage(BaseModel):
    items: list[VerificationCandidate]
    state: Literal["ready", "not_evaluated"] = "ready"
    reason: str | None = None
    next_cursor: str | None = None
    available_targets: list[dict[str, Any]] = Field(default_factory=list)
