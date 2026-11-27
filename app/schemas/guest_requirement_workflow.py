"""公開要件レビューで扱う発行入力とゲスト向け表現。"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.artifact_publication import PublicationNodeRead
from app.schemas.artifact_publication_access import PublicationShareInput
from app.schemas.requirement_workflow import (
    RequirementFormalApprovalDecision,
    RequirementFormalApprovalException,
    RequirementReviewItemState,
    WorkflowScope,
    WorkflowStatus,
)


class GuestRequirementReviewer(BaseModel):
    """既存の公開許可を持つレビュー担当候補。"""

    grant_id: UUID
    participation_id: UUID
    name: str
    expires_at: datetime


class GuestRequirementReviewOptions(BaseModel):
    """担当候補と発行前に確認する公開版本文の識別子。"""

    public_digest: str
    reviewers: list[GuestRequirementReviewer]


class GuestRequirementReviewCreate(BaseModel):
    """公開された版の選択範囲にレビューを依頼する。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    publication_id: UUID
    target_node_id: UUID
    grant_ids: list[UUID] = Field(default_factory=list, max_length=25)
    sharing: PublicationShareInput | None = None
    request_key: UUID
    expected_public_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    note: str | None = Field(default=None, max_length=2000)
    due_on: date | None = None

    @model_validator(mode="after")
    def check_recipients(self):
        count = len(self.grant_ids) + (
            len(self.sharing.recipients) if self.sharing else 0
        )
        if not 1 <= count <= 25 or len(set(self.grant_ids)) != len(self.grant_ids):
            raise ValueError("担当者を重複せず1名以上25名まで指定してください")
        return self


class GuestRequirementDecisionCancel(BaseModel):
    """外部判断依頼の確認時版と取消理由。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


class SharedRequirementReviewSummary(BaseModel):
    """本人に届いた依頼の一覧に必要な情報。"""

    id: UUID
    publication_id: UUID
    title: str
    version_label: str
    status: WorkflowStatus
    scope_kind: WorkflowScope
    due_on: date | None
    created_at: datetime
    expires_at: datetime
    responded: bool
    public_project_name: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    captured_at: datetime | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class SharedRequirementReviewList(BaseModel):
    """有効な共有許可からアクセスできる本人の依頼。"""

    items: list[SharedRequirementReviewSummary]
    total: int


class SharedRequirementReviewRead(SharedRequirementReviewSummary):
    """内部参照を含まない、公開された固定範囲のレビュー。"""

    note: str | None
    target_node_id: UUID
    version: int
    can_respond: bool
    can_discuss: bool
    requirement_count: int
    confirmed_count: int
    changes_requested_count: int
    authorization_revision: str


class SharedRequirementReviewNodes(BaseModel):
    """公開IDで参照する本文と本人の確認状況。"""

    items: list[PublicationNodeRead]
    states: list[RequirementReviewItemState]
    next_cursor: str | None
    authorization_revision: str


class SharedRequirementReviewThread(BaseModel):
    """本人の指摘と、それに対する返信。"""

    id: UUID
    node_id: UUID
    kind: Literal["comment", "correction"]
    body: str
    status: Literal["open", "in_progress", "awaiting_confirmation", "resolved"]
    available_actions: list[Literal["reply", "reopen", "resolve"]]
    version: int
    created_at: datetime


class SharedRequirementReviewThreads(BaseModel):
    """指定箇所に本人が残した指摘の一覧。"""

    items: list[SharedRequirementReviewThread]
    total: int


class SharedRequirementReviewReplies(BaseModel):
    """本人の指摘への返信。内部の操作理由とユーザーIDは含まない。"""

    items: list[str]
    total: int


class GuestRequirementFormalApprovalInput(BaseModel):
    """文書全体の公開版に対し、社内・外部の承認者を指定する。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    publication_id: UUID
    expected_public_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    grant_ids: list[UUID] = Field(default_factory=list, max_length=25)
    sharing: PublicationShareInput | None = None
    participant_ids: list[Annotated[int, Field(gt=0)]] = Field(
        default_factory=list, max_length=24
    )
    due_on: date | None = None
    note: str | None = Field(default=None, max_length=2000)
    exceptions: list[RequirementFormalApprovalException] = Field(
        default_factory=list, max_length=100
    )

    @model_validator(mode="after")
    def check_recipients(self):
        guest_count = len(self.grant_ids) + (
            len(self.sharing.recipients) if self.sharing else 0
        )
        if (
            len(set(self.grant_ids)) != len(self.grant_ids)
            or len(set(self.participant_ids)) != len(self.participant_ids)
            or not guest_count
            or guest_count + len(self.participant_ids) > 25
        ):
            raise ValueError(
                "ゲスト1名以上を含め、承認者を重複せず25名まで指定してください"
            )
        if self.sharing is not None and self.due_on is None:
            raise ValueError("共有と同時に依頼するときは回答期限を指定してください")
        if len({(item.kind, item.id) for item in self.exceptions}) != len(
            self.exceptions
        ):
            raise ValueError("同じ例外対象を重複して指定できません")
        return self


class GuestRequirementFormalApprovalCreate(GuestRequirementFormalApprovalInput):
    """公開本文と承認条件の確認結果を固定する。"""

    request_key: UUID
    expected_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    confirmed_content: Literal[True]


class SharedRequirementFormalApprovalDecision(RequirementFormalApprovalDecision):
    """指定本人の判断を公開版の識別子に結び付ける。"""

    expected_public_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class SharedRequirementFormalApprovalRead(SharedRequirementReviewSummary):
    """本人に許可された承認対象と現在も閲覧できる比較版。"""

    note: str | None
    version: int
    can_decide: bool
    public_digest: str
    previous_publication_id: UUID | None
    authorization_revision: str


class SharedRequirementApprovalChange(BaseModel):
    """公開ノードだけで参照できる追加・変更・削除。"""

    kind: Literal["added", "changed", "removed"]
    before_id: UUID | None
    after_id: UUID | None
    fields: list[str]


class SharedRequirementApprovalComparison(BaseModel):
    """二つの許可に結び付けた公開版の差分一覧。"""

    previous_publication_id: UUID
    previous_authorization_revision: str
    authorization_revision: str
    items: list[SharedRequirementApprovalChange]
    total: int
