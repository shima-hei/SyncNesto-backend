"""外部向け承認の限定入力と、内部正本を露出しない応答。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.fixed_approval import ApprovalExceptionInput

ApprovalKind = Literal[
    "requirement", "requirement_document", "specification", "test_design"
]
ApprovalStatus = Literal[
    "requested", "approved", "approved_with_exceptions", "rejected", "cancelled"
]


class GuestApprovalTarget(BaseModel):
    """公開版内の不透明な対象IDだけを指定する。"""

    model_config = ConfigDict(extra="forbid")
    publication_id: UUID
    target_node_id: UUID


class GuestApprovalBasisInput(GuestApprovalTarget):
    """文書根拠を完全な集合hashに結び付けて分割する。"""

    cursor: str | None = Field(default=None, max_length=1000)
    page_size: int = Field(default=50, ge=1, le=100)


class GuestApprovalInput(GuestApprovalTarget):
    """現在許可済みの相手と、相手に示す依頼文・例外。"""

    grant_id: UUID
    request_note: str | None = Field(default=None, max_length=2000)
    exceptions: list[ApprovalExceptionInput] = Field(
        default_factory=list, max_length=1000
    )

    @model_validator(mode="after")
    def unique_exceptions(self):
        """対象や理由を省略・重複させない。"""
        if len({item.node_id for item in self.exceptions}) != len(self.exceptions):
            raise ValueError("例外の要件が重複しています")
        if any(not item.reason.strip() for item in self.exceptions):
            raise ValueError("例外理由を入力してください")
        return self


class GuestApprovalCreate(GuestApprovalInput):
    """相手と同じ表現を確認した構成へのみ申請する。"""

    request_id: UUID
    expected_preview_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_public_scope: bool


class GuestApprovalCandidate(BaseModel):
    """公開許可を持つ候補。メールやUser IDを返さない。"""

    grant_id: UUID
    name: str
    expires_at: datetime


class GuestApprovalCandidates(BaseModel):
    """現在対象を読める候補だけのページ。"""

    items: list[GuestApprovalCandidate]
    total: int
    page: int
    page_size: int


class GuestApprovalBasis(BaseModel):
    """今回共有した要件の合意要約。過去の他人の履歴を含めない。"""

    node_id: UUID
    title: str
    state: str
    reason: str | None = None


class GuestApprovalBases(BaseModel):
    """分割した根拠と全体digest。"""

    items: list[GuestApprovalBasis]
    total: int
    counts: dict[str, int]
    scope_digest: str
    next_cursor: str | None


class GuestApprovalPreview(BaseModel):
    """相手に見せる依頼の確認要約。本文は既存の公開readerを使う。"""

    publication_id: UUID
    target_node_id: UUID
    target_kind: ApprovalKind
    title: str
    approver_name: str
    requester_name: str
    request_note: str | None
    exception_count: int
    basis_counts: dict[str, int]
    public_scope_digest: str
    preview_digest: str


class SharedApprovalRead(BaseModel):
    """現在の指定本人にだけ返す今回の依頼。"""

    id: UUID
    publication_id: UUID
    target_node_id: UUID
    target_kind: ApprovalKind
    title: str
    requester_name: str
    request_note: str | None
    status: ApprovalStatus
    version: int
    created_at: datetime
    captured_at: datetime
    finalized_at: datetime | None
    decision_reason: str | None = None
    exception_count: int
    can_decide: bool
    expires_at: datetime
    authorization_revision: str
    public_scope_digest: str


class SharedApprovalPage(BaseModel):
    """本人と現在資格で絞った集合だけの件数。"""

    items: list[SharedApprovalRead]
    total: int
    next_cursor: str | None


class SharedApprovalDecisionPreview(BaseModel):
    """判断する本人・版・資格・観測結果に束縛した短期確認。"""

    version: int
    review_proof: str
    expires_at: datetime
    body_comparison: Literal["equal", "changed", "unavailable"]
    exception_count: int


class SharedApprovalDecision(BaseModel):
    """本人や状態を入力させず、今回の意思だけを受け取る。"""

    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    operation_id: UUID
    review_proof: str = Field(min_length=1, max_length=2000)
    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=2000)
    confirm_fixed_scope: bool
    confirm_changed_or_unknown: bool = False
    confirm_exceptions: bool = False
