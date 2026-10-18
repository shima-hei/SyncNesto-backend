"""固定対象・指定本人・例外確認を明示する内部承認API契約。"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApprovalExceptionInput(BaseModel):
    """文書内の未合意等を隠さず、対象ごとの理由を受け取る。"""

    model_config = ConfigDict(extra="forbid")
    node_id: UUID
    reason: str = Field(min_length=1, max_length=2000)


class FixedApprovalScopeInput(BaseModel):
    """本文やactorを任意入力させず、保存済みの対象だけを指定する。"""

    model_config = ConfigDict(extra="forbid")
    release_id: UUID
    target_node_id: UUID
    preservation_id: UUID | None = None
    attachments_none: bool = False
    approver_id: int = Field(gt=0)
    legacy_import_id: UUID | None = None
    request_note: str | None = Field(default=None, max_length=2000)
    exceptions: list[ApprovalExceptionInput] = Field(
        default_factory=list, max_length=1000
    )

    @model_validator(mode="after")
    def explicit_scope(self):
        """添付あり／なしと、例外の重複を曖昧にしない。"""
        if (self.preservation_id is None) != self.attachments_none:
            raise ValueError("添付構成、または添付なしを明示してください")
        if len({item.node_id for item in self.exceptions}) != len(self.exceptions):
            raise ValueError("例外の要件が重複しています")
        if any(not item.reason.strip() for item in self.exceptions):
            raise ValueError("例外理由を入力してください")
        return self


class FixedApprovalCreate(FixedApprovalScopeInput):
    """確認結果と再送IDを付けて申請する。"""

    request_id: UUID
    expected_preview_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class FixedApprovalPreview(BaseModel):
    """本文を再編集させず、対象・根拠・例外の構成を提示する。"""

    title: str
    target_kind: Literal["requirement", "requirement_document"]
    source_id: int
    scope_manifest: dict[str, Any]
    scope_digest: str
    preview_digest: str
    basis_counts: dict[str, int]


class FixedApprovalEventRead(BaseModel):
    """監査ログとは別の、実際の判断イベント。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    seq: int
    actor_id: int
    actor_name: str | None = None
    action: str
    reason: str | None
    observation: dict[str, Any]
    occurred_at: datetime
    recorded_at: datetime


class FixedApprovalRead(BaseModel):
    """現在の閲覧権限を確認した新依頼の詳細。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: int
    release_id: UUID
    target_node_id: UUID
    target_kind: str
    source_id: int
    preservation_id: UUID | None
    requester_id: int
    approver_id: int
    requester_name: str | None = None
    approver_name: str | None = None
    request_note: str | None
    status: str
    version: int
    scope_manifest: dict[str, Any]
    scope_digest: str
    created_at: datetime
    finalized_at: datetime | None
    legacy_import_id: UUID | None
    events: list[FixedApprovalEventRead] = Field(default_factory=list)
    can_decide: bool = False
    can_cancel: bool = False


class FixedApprovalHistoryItem(BaseModel):
    """従来記録と版固定依頼を、同じIDや同じ意味として扱わない。"""

    scheme: Literal["fixed", "legacy"]
    id: str
    title: str
    target_kind: str
    source_id: int
    status: str
    requester_id: int
    approver_id: int
    created_at: datetime
    finalized_at: datetime | None = None
    evidence: dict[str, Any] | None = None
    import_id: UUID | None = None
    migrated_to: UUID | None = None


class FixedApprovalHistoryPage(BaseModel):
    """許可された集合だけから算出した履歴。"""

    items: list[FixedApprovalHistoryItem]
    total: int
    page: int
    page_size: int


class ApprovalBasisRead(BaseModel):
    """要件本文に一致する合意根拠、または不足・例外。"""

    node_id: UUID
    source_id: int
    source_version: int | None
    title: str
    subject_digest: str
    state: str
    agreed_approval_id: UUID | None = None
    agreed_event_id: UUID | None = None
    reason: str | None = None


class ApprovalBasisPage(BaseModel):
    """全体hashに結び付けた要件別根拠のページ。"""

    items: list[ApprovalBasisRead]
    total: int
    counts: dict[str, int]
    scope_digest: str
    next_cursor: str | None


class ApprovalCandidate(BaseModel):
    """権限を満たす本人だけを返し、不要なemailを含めない。"""

    id: int
    name: str


class ApprovalCandidates(BaseModel):
    """指定可能な本人をページ取得する。"""

    items: list[ApprovalCandidate]
    total: int
    page: int
    page_size: int


class ApprovalDecisionPreview(BaseModel):
    """判断対象と現在内容の比較。添付bytesの一致を推測しない。"""

    approval_id: UUID
    version: int
    review_digest: str
    observation: dict[str, Any]
    exception_count: int


class ApprovalDecisionInput(BaseModel):
    """確認した内容にだけ、指定本人として判断する。"""

    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    operation_id: UUID
    expected_review_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=2000)
    confirm_fixed_scope: bool
    confirm_changed_or_unknown: bool = False
    confirm_exceptions: bool = False


class ApprovalCancelInput(BaseModel):
    """理由付きで、未判断の依頼だけを取消す。"""

    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    operation_id: UUID
    reason: str = Field(min_length=1, max_length=2000)


class LegacyApprovalPreviewInput(BaseModel):
    """旧IDと、新方式へ切り替える場合の明示した対象。"""

    model_config = ConfigDict(extra="forbid")
    legacy_approval_id: int = Field(gt=0)
    mode: Literal["archive", "switch"]
    new_request: FixedApprovalScopeInput | None = None

    @model_validator(mode="after")
    def require_switch_target(self):
        """履歴保存だけの処理に、新たな判断範囲を混ぜない。"""
        if (self.mode == "switch") != (self.new_request is not None):
            raise ValueError("切替時だけ新しい固定対象を指定してください")
        return self


class LegacyApprovalCreate(LegacyApprovalPreviewInput):
    """preview以後の旧行・ログ変更を検知して取り込む。"""

    request_id: UUID
    expected_preview_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class LegacyApprovalPreview(BaseModel):
    """原文を大量返却せず、移行分類と確認hashを返す。"""

    legacy_approval_id: int
    source: dict[str, Any]
    evidence: dict[str, Any]
    preview_digest: str
    new_request: FixedApprovalPreview | None = None


class LegacyApprovalImportRead(BaseModel):
    """取込時点の証拠を、1MiBを超えないページで返す。"""

    id: UUID
    legacy_approval_id: int
    mode: str
    source: dict[str, Any]
    evidence: dict[str, Any]
    created_by: int
    created_at: datetime
    migrated_to: UUID | None
    logs: list[dict[str, Any]]
    total_logs: int
    next_cursor: str | None
