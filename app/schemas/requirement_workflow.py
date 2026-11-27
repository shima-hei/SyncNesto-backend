"""要件定義のレビュー依頼と回答のAPI契約。"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.artifact_release import ArtifactSource

WorkflowKind = Literal["review", "formal_approval"]
WorkflowScope = Literal["requirement_document", "section", "requirement"]
WorkflowListView = Literal["all", "assigned", "requested"]
WorkflowStatus = Literal[
    "requested", "completed", "changes_requested", "approved", "returned", "cancelled"
]
ParticipantStatus = Literal[
    "pending", "confirmed", "changes_requested", "approved", "returned"
]
PositiveId = Annotated[int, Field(gt=0)]


class RequirementReviewInput(BaseModel):
    """依頼前に、範囲・担当者・添付を指定する。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    document_version: PositiveId
    scope_kind: WorkflowScope = "requirement_document"
    scope_id: PositiveId | None = None
    participant_ids: list[PositiveId] = Field(min_length=1, max_length=25)
    due_on: date | None = None
    note: str | None = Field(default=None, max_length=2000)
    included_sources: list[ArtifactSource] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def check_scope_and_participants(self):
        """対象の組と担当者を一意に特定する。"""
        if (self.scope_kind == "requirement_document") != (self.scope_id is None):
            raise ValueError("章または個別要件を選択してください")
        if len(set(self.participant_ids)) != len(self.participant_ids):
            raise ValueError("同じ担当者を重複して指定できません")
        if any(
            source.kind not in {"specification", "test_design"}
            for source in self.included_sources
        ):
            raise ValueError("添付できるのは設計書とテスト設計書です")
        return self


class RequirementReviewRequestCreate(RequirementReviewInput):
    """確認済み入力と利用者単位の再送キーを保持する。"""

    request_key: UUID
    expected_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class RequirementWorkflowRecipient(BaseModel):
    """案件内で依頼を受けられるユーザー。"""

    id: int
    name: str


ApprovalBlockerKind = Literal["correction", "open_issue"]


class RequirementFormalApprovalBlocker(BaseModel):
    """正式承認までに対応する指摘または未決事項。"""

    kind: ApprovalBlockerKind
    id: str
    title: str


class RequirementFormalApprovalException(BaseModel):
    """承認時点で残す事項と、例外の対応責任を確定する。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: ApprovalBlockerKind
    id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=1, max_length=2000)
    owner_id: PositiveId
    due_on: date


class RequirementFormalApprovalInput(BaseModel):
    """文書全体の正式承認に指定する条件。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    document_version: PositiveId
    participant_ids: list[PositiveId] = Field(min_length=1, max_length=25)
    due_on: date | None = None
    note: str | None = Field(default=None, max_length=2000)
    included_sources: list[ArtifactSource] = Field(default_factory=list, max_length=20)
    exceptions: list[RequirementFormalApprovalException] = Field(
        default_factory=list, max_length=100
    )

    @model_validator(mode="after")
    def validate_selection(self):
        """承認者と例外対象を一意に指定し、添付の種別を限定する。"""
        if len(set(self.participant_ids)) != len(self.participant_ids):
            raise ValueError("同じ承認者を重複して指定できません")
        if len({(item.kind, item.id) for item in self.exceptions}) != len(
            self.exceptions
        ):
            raise ValueError("同じ例外対象を重複して指定できません")
        if any(
            source.kind not in {"specification", "test_design"}
            for source in self.included_sources
        ):
            raise ValueError("添付できるのは設計書とテスト設計書です")
        return self


class RequirementFormalApprovalCreate(RequirementFormalApprovalInput):
    """確認した承認前条件と再送キーを指定する。"""

    request_key: UUID
    expected_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed_content: Literal[True]


class RequirementFormalApprovalBasis(BaseModel):
    """申請時点で保存した審査条件と例外。"""

    review_required: bool
    reviewed_request_id: UUID | None
    reviewed_request_ids: list[UUID] = Field(default_factory=list)
    previous_baseline_id: UUID | None
    exceptions: list[RequirementFormalApprovalException]
    exception_versions: dict[str, int] = Field(default_factory=dict)


class RequirementFormalApprovalPreview(BaseModel):
    """保存なしで、承認対象と申請できる条件を提示する。"""

    document_id: int
    document_version: int
    title: str
    section_count: int
    requirement_count: int
    recipients: list[RequirementWorkflowRecipient]
    review_required: bool
    reviewed_request_id: UUID | None
    reviewed_request_ids: list[UUID] = Field(default_factory=list)
    blockers: list[RequirementFormalApprovalBlocker]
    pending_approval_id: UUID | None
    previous_baseline_id: UUID | None
    can_request: bool
    confirmation_digest: str


class RequirementFormalApprovalDecision(BaseModel):
    """本人の承認・差し戻しに共通する判断内容。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operation_key: UUID
    version: PositiveId
    decision: Literal["approved", "returned"]
    confirmed_content: Literal[True]
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_return_reason(self):
        """差し戻し時は判断した本人の理由を必須にする。"""
        if self.decision == "returned" and not self.reason:
            raise ValueError("差し戻す理由を入力してください")
        return self


class RequirementFormalApprovalDecisionInput(RequirementFormalApprovalDecision):
    """社内の判断を確認済みの固定版と照合する。"""

    expected_release_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class RequirementReviewPreview(BaseModel):
    """発行前に確認した範囲と担当者。"""

    document_id: int
    document_version: int
    title: str
    target_title: str
    scope_kind: WorkflowScope
    scope_id: int
    requirement_count: int
    due_on: date | None
    recipients: list[RequirementWorkflowRecipient]
    confirmation_digest: str


class RequirementWorkflowParticipantRead(BaseModel):
    """担当者の実際の回答状況。"""

    user_id: int
    name: str | None
    status: ParticipantStatus
    response_note: str | None
    responded_at: datetime | None


class RequirementWorkflowRead(BaseModel):
    """固定した対象と現在の依頼状況。"""

    id: UUID
    project_id: int
    document_id: int
    release_id: UUID
    target_node_id: UUID
    document_version: int
    version_label: str
    title: str
    target_title: str
    scope_kind: WorkflowScope
    scope_id: int
    kind: WorkflowKind
    status: WorkflowStatus
    requester_id: int
    requester_name: str | None
    note: str | None
    due_on: date | None
    version: int
    created_at: datetime
    finalized_at: datetime | None
    participants: list[RequirementWorkflowParticipantRead]
    approval_basis: RequirementFormalApprovalBasis | None = None
    external_review: bool = False
    external_approval: bool = Field(default=False, exclude_if=lambda value: not value)


class RequirementFormalApprovalDecisionPreview(BaseModel):
    """本人が読む承認対象と前回の開発基準。"""

    request: RequirementWorkflowRead
    previous_baseline: RequirementWorkflowRead | None
    release_digest: str
    can_decide: bool


class RequirementWorkflowList(BaseModel):
    """案件内の依頼と、条件に一致する件数。"""

    items: list[RequirementWorkflowRead]
    total: int
    page: int
    page_size: int


class RequirementManagementCorrection(BaseModel):
    """未決事項と同じ画面で確認するレビュー指摘。"""

    id: UUID
    request_id: UUID
    node_id: UUID
    version_label: str
    title: str
    assignee_name: str | None
    status: Literal["open", "in_progress", "awaiting_confirmation", "resolved"]


class RequirementReviewManagementRead(BaseModel):
    """定義書のレビュー担当、正式承認、対応必須事項の状況。"""

    current_review: RequirementWorkflowRead | None
    active_review_count: int
    formal_approval: RequirementWorkflowRead | None
    corrections: list[RequirementManagementCorrection]
    correction_total: int
    open_correction_count: int
    resolved_correction_count: int
    open_issue_count: int
    resolved_issue_count: int
    blocking_issue_count: int


class RequirementBaselineChange(BaseModel):
    """対象と、正式承認版から変更された属性名。"""

    kind: Literal["requirement_document", "section", "requirement"]
    id: int
    title: str
    requirement_code: str | None
    change: Literal["added", "changed", "removed"]
    fields: list[str]


class RequirementItemWorkflowState(BaseModel):
    """現在の要件と、別々に記録された確認・正式承認の状態。"""

    requirement_id: int
    authoring: Literal["draft", "defined", "excluded"]
    review: Literal[
        "not_reviewed", "changes_requested", "confirmed", "changed", "restricted"
    ]
    approval: Literal["not_approved", "approved", "changed", "restricted"]
    review_request_id: UUID | None = None
    review_item_node_id: UUID | None = None
    approved_request_id: UUID | None = None
    approved_version_label: str | None = None
    changed_fields: list[str] = Field(default_factory=list)


class RequirementBaselineComparison(BaseModel):
    """開発基準はそのままに、保存した編集内容との差を表示する。"""

    baseline_id: UUID
    baseline_label: str
    baseline_count: int
    current_count: int
    requirement_added: int
    requirement_changed: int
    requirement_removed: int
    items: list[RequirementBaselineChange]
    total: int
    page: int
    page_size: int


class RequirementReviewVersionInput(BaseModel):
    """見ていた依頼の版を指定する。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: PositiveId


class RequirementReviewConfirmationInput(RequirementReviewVersionInput):
    """指定要件の確認または確認の取消。"""

    confirmed: bool


class RequirementReviewSubmitInput(RequirementReviewVersionInput):
    """明示した担当者のレビュー結果提出。"""

    confirm_remaining: bool = False
    note: str | None = Field(default=None, max_length=2000)


class RequirementReviewItemState(BaseModel):
    """固定要件ごとの本人確認と対応必須の修正指摘の有無。"""

    node_id: UUID
    status: Literal["pending", "confirmed", "changes_requested"]


class RequirementReviewProgress(BaseModel):
    """担当者の現在の確認状況と画面に載せる要件の状態。"""

    version: int
    can_respond: bool
    can_discuss: bool
    participant_status: ParticipantStatus | None
    requirement_count: int
    confirmed_count: int
    changes_requested_count: int
    open_correction_count: int
    items: list[RequirementReviewItemState] = Field(default_factory=list)


class RequirementReviewAnchor(BaseModel):
    """固定本文内の文字位置を指定する。"""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=40)
    quote: str = Field(min_length=1, max_length=1000)
    quote_start: int = Field(ge=0)


ReviewThreadKind = Literal["comment", "correction"]
ReviewThreadStatus = Literal["open", "in_progress", "awaiting_confirmation", "resolved"]
ReviewThreadAction = Literal[
    "reply", "start", "respond", "reopen", "resolve", "force_resolve"
]


class RequirementReviewThreadCreate(RequirementReviewVersionInput):
    """対象版へのコメント、または対応必須の修正依頼。"""

    node_id: UUID
    kind: ReviewThreadKind
    body: str = Field(min_length=1, max_length=4000)
    anchor: RequirementReviewAnchor | None = None


class RequirementReviewThreadActionInput(RequirementReviewVersionInput):
    """返信または修正指摘の状態変更。"""

    action: ReviewThreadAction
    body: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def check_reason(self):
        """返信・修正への回答・再指摘・例外説明には本文を必要とする。"""
        if (
            self.action in {"reply", "respond", "reopen", "force_resolve"}
            and not self.body
        ):
            raise ValueError("コメントまたは理由を入力してください")
        return self


class RequirementReviewThreadEventRead(BaseModel):
    """返信・操作の事実、本文、実行者。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_id: int
    action: ReviewThreadAction
    body: str | None
    created_at: datetime


class RequirementReviewThreadRead(BaseModel):
    """固定版の対象とレビュー指摘の状態。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    request_id: UUID
    node_id: UUID
    author_id: int
    assignee_id: int | None
    kind: ReviewThreadKind
    status: ReviewThreadStatus
    field: str | None
    quote: str | None
    quote_start: int | None
    body: str
    version: int
    created_at: datetime


class RequirementReviewThreadList(BaseModel):
    """レビュー指摘の一覧と件数。"""

    items: list[RequirementReviewThreadRead]
    total: int
    page: int
    page_size: int


class RequirementReviewThreadDetail(BaseModel):
    """レビュー指摘の詳細と操作履歴。"""

    thread: RequirementReviewThreadRead
    events: list[RequirementReviewThreadEventRead]
    event_count: int
    available_actions: list[ReviewThreadAction]
