"""版を束ねた設計レビュー・承認の入出力。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.design_content import ArtifactKind

DesignReviewKind = Literal["review", "approval"]
DesignReviewOutcome = Literal["reviewed", "approved", "changes_requested", "cancelled"]
DesignReviewStatus = Literal[
    "pending", "reviewed", "approved", "changes_requested", "cancelled"
]


class DesignReviewReference(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    specification_id: int = Field(gt=0)
    version: int = Field(gt=0)


class DesignReviewTargetRead(DesignReviewReference):
    title: str
    kind: ArtifactKind


class DesignReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    kind: DesignReviewKind
    reviewer_id: int = Field(gt=0)
    note: str = Field(default="", max_length=2000)
    targets: list[DesignReviewReference] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_targets(self):
        if len({item.specification_id for item in self.targets}) != len(self.targets):
            raise ValueError("同じ設計書を重複して依頼できません")
        return self


class DesignReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: DesignReviewOutcome
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def require_reason(self):
        if self.status == "changes_requested" and not self.note:
            raise ValueError("修正を依頼する理由を入力してください")
        return self


class DesignReviewer(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class DesignReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: DesignReviewKind
    status: DesignReviewStatus
    requester_id: int
    reviewer_id: int
    requester_name: str
    reviewer_name: str
    note: str
    decision_note: str | None
    created_at: datetime
    decided_at: datetime | None
    targets: list[DesignReviewTargetRead]
    assigned_to_me: bool
    can_cancel: bool
