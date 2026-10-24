"""判断対象を明示選択し、固定した依頼を本人だけが確定する契約。"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.requirement_verification import StrictInput


class VerdictEvidence(StrictInput):
    set_id: UUID
    entry_id: UUID


class VerdictSelection(StrictInput):
    plan_id: UUID
    plan_version: int = Field(gt=0)
    judge_id: int = Field(gt=0)
    entries: list[VerdictEvidence] = Field(default_factory=list, max_length=1000)
    previous_request_id: UUID | None = None
    reviewed_decision_id: UUID | None = None
    reason: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def unique(self):
        if len({(e.set_id, e.entry_id) for e in self.entries}) != len(self.entries):
            raise ValueError("同じ根拠が重複しています")
        if len({e.set_id for e in self.entries}) > 50:
            raise ValueError("証拠セットは50件までです")
        return self


class VerdictCreate(VerdictSelection):
    operation_id: UUID
    expected_preview_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class VerdictOperation(StrictInput):
    version: int = Field(gt=0)
    operation_id: UUID
    reason: str = Field(min_length=1, max_length=4000)


class VerdictReview(StrictInput):
    version: int = Field(gt=0)
    review_version: int = Field(ge=0)


class VerdictDecision(VerdictOperation):
    result: Literal["passed", "failed", "not_applicable"]
    review_version: int = Field(ge=0)
    acknowledge_requirement_difference: bool = False
    confirmed_not_applicable_rows: list[UUID] = Field(
        default_factory=list, max_length=100
    )
    unresolved_rows: list[UUID] = Field(default_factory=list, max_length=100)


class VerdictPreview(StrictInput):
    preview_digest: str
    plan_id: UUID
    plan_version: int
    source_requirement_id: int
    subject_digest: str
    checklist_digest: str
    judge_id: int
    judge_name: str
    readiness: dict[str, Any]
    comparison: dict[str, Any]
    entries: list[dict[str, Any]]
    file_count: int


class VerdictSummary(StrictInput):
    id: UUID
    source_requirement_id: int
    plan_id: UUID
    plan_version: int
    subject_digest: str
    status: Literal["requested", "decided", "cancelled", "withdrawn"]
    version: int
    result: Literal["passed", "failed", "not_applicable"] | None
    judge_id: int
    judge_name: str
    requester_name: str
    created_at: datetime
    decided_at: datetime | None
    previous_request_id: UUID | None
    reviewed_decision_id: UUID | None


class VerdictPage(StrictInput):
    enabled: bool
    state: Literal["ready", "disabled", "restricted"]
    can_prepare: bool = False
    items: list[VerdictSummary] = Field(default_factory=list)
    next_cursor: str | None = None


class VerdictCandidate(StrictInput):
    id: int
    name: str
    is_self: bool


class VerdictCandidates(StrictInput):
    items: list[VerdictCandidate]


class VerdictDetail(VerdictSummary):
    fixed_subject: dict[str, Any]
    checklist: dict[str, Any]
    basis_digest: str
    reason: str
    readiness: dict[str, Any]
    comparison: dict[str, Any]
    validity: str
    entries: list[dict[str, Any]]
    files: list[dict[str, Any]]
    review_version: int
    review_expires_at: datetime | None
    verified_files: int
    review_error: str | None
    decision_reason: str | None
    can_review: bool
    can_cancel: bool
    can_withdraw: bool


class VerdictEventRead(StrictInput):
    id: UUID
    version: int
    event: str
    actor_name: str
    created_at: datetime
    reason: str
    snapshot: dict[str, Any]


class VerdictEvents(StrictInput):
    items: list[VerdictEventRead]
    next_cursor: str | None = None


class RequirementVerdict(StrictInput):
    requirement_id: int
    state: str
    result: str | None = None
    request_id: UUID | None = None
    plan_version: int | None = None
    subject_digest: str | None = None
    comparison: dict[str, Any] = Field(default_factory=dict)
    files_last_checked_at: datetime | None = None
    files_rechecked: bool = False


class RequirementVerdicts(StrictInput):
    enabled: bool
    state: Literal["ready", "disabled", "restricted"]
    items: list[RequirementVerdict] = Field(default_factory=list)
