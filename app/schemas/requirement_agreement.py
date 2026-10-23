"""現在の内容に対する判断。本文・個人情報・不可視の存在を含めない。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

AgreementState = Literal[
    "agreed",
    "conflicting",
    "rejected",
    "pending",
    "cancelled",
    "changed",
    "legacy_unverified",
    "unagreed",
    "not_evaluated",
]


class RequirementAgreementResult(BaseModel):
    """指定された本人の判断であり、関係者全員の合意を意味しない。"""

    state: AgreementState
    reason: Literal[
        "current_subject",
        "other_subject",
        "legacy",
        "no_record",
        "size_limit",
        "concurrent_change",
        "invalid_evidence",
    ]
    approval_id: UUID | None = None
    event_id: UUID | None = None


class RequirementAgreementRow(BaseModel):
    """同じ取得時点で比較した社内/外部の結果。"""

    requirement_id: int
    version: int
    internal: RequirementAgreementResult
    external: RequirementAgreementResult


class RequirementAgreementPage(BaseModel):
    """制限時はID・状態・件数を返さない。"""

    enabled: bool
    state: Literal["disabled", "restricted", "ready"]
    evaluated_at: datetime
    items: list[RequirementAgreementRow] = Field(default_factory=list)
