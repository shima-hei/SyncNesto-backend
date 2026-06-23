"""要件定義schemaを定義するモジュール。"""

from datetime import date, datetime

from pydantic import BaseModel

from app.schemas.user import UserSummary


class RequirementDocumentBase(BaseModel):
    """要件定義書schemaの共通フィールドを定義する基底schema。"""

    title: str
    document_code: str
    status: str = "draft"
    purpose: str | None = None
    target_system_name: str | None = None
    client_name: str | None = None
    vendor_name: str | None = None
    author_id: int | None = None
    reviewer_id: int | None = None
    approver_id: int | None = None
    approved_at: datetime | None = None


class RequirementDocumentCreate(RequirementDocumentBase):
    """要件定義書作成リクエストで受け取るschema。"""


class RequirementDocumentUpdate(BaseModel):
    """要件定義書更新リクエストで受け取るschema。"""

    version: int
    title: str | None = None
    document_code: str | None = None
    status: str | None = None
    purpose: str | None = None
    target_system_name: str | None = None
    client_name: str | None = None
    vendor_name: str | None = None
    author_id: int | None = None
    reviewer_id: int | None = None
    approver_id: int | None = None
    approved_at: datetime | None = None


class RequirementDocumentRead(RequirementDocumentBase):
    """要件定義書読み取り時に返すschema。"""

    id: int
    project_id: int
    version: int
    created_by: int | None = None
    updated_by: int | None = None
    created_at: datetime
    updated_at: datetime
    author: UserSummary | None = None
    reviewer: UserSummary | None = None
    approver: UserSummary | None = None

    model_config = {"from_attributes": True}


class RequirementDocumentListResponse(BaseModel):
    """要件定義書一覧レスポンスschema。"""

    items: list[RequirementDocumentRead]
    total: int
    page: int
    page_size: int


class RequirementDocumentExportCreate(BaseModel):
    """要件定義書出力リクエストで受け取るschema。"""

    format: str = "markdown"
    include_comments: bool = False
    include_change_logs: bool = False


class RequirementDocumentExportRead(BaseModel):
    """要件定義書出力レスポンスschema。"""

    format: str
    content: str


class RequirementSectionBase(BaseModel):
    """要件定義セクションschemaの共通フィールドを定義する基底schema。"""

    title: str
    section_type: str
    content: str | None = None
    sort_order: int = 0
    status: str = "draft"


class RequirementSectionCreate(RequirementSectionBase):
    """要件定義セクション作成リクエストで受け取るschema。"""


class RequirementSectionUpdate(BaseModel):
    """要件定義セクション更新リクエストで受け取るschema。"""

    version: int
    title: str | None = None
    section_type: str | None = None
    content: str | None = None
    sort_order: int | None = None
    status: str | None = None


class RequirementSectionSortItem(BaseModel):
    """要件定義セクションの並び順更新項目schema。"""

    section_id: int
    sort_order: int
    version: int


class RequirementSectionSortUpdate(BaseModel):
    """要件定義セクションの並び順更新リクエストschema。"""

    items: list[RequirementSectionSortItem]


class RequirementSectionRead(RequirementSectionBase):
    """要件定義セクション読み取り時に返すschema。"""

    id: int
    document_id: int
    version: int
    created_by: int | None = None
    updated_by: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RequirementBase(BaseModel):
    """要件schemaの共通フィールドを定義する基底schema。"""

    section_id: int | None = None
    requirement_code: str
    requirement_type: str
    category: str | None = None
    title: str
    description: str | None = None
    rationale: str | None = None
    acceptance_criteria: str | None = None
    priority: str = "must"
    status: str = "draft"
    source: str | None = None
    owner_id: int | None = None
    approved_by: int | None = None
    approved_at: datetime | None = None


class RequirementCreate(RequirementBase):
    """要件作成リクエストで受け取るschema。"""

    document_id: int


class RequirementUpdate(BaseModel):
    """要件更新リクエストで受け取るschema。"""

    version: int
    section_id: int | None = None
    requirement_code: str | None = None
    requirement_type: str | None = None
    category: str | None = None
    title: str | None = None
    description: str | None = None
    rationale: str | None = None
    acceptance_criteria: str | None = None
    priority: str | None = None
    status: str | None = None
    source: str | None = None
    owner_id: int | None = None
    approved_by: int | None = None
    approved_at: datetime | None = None
    change_summary: str | None = None
    reason: str | None = None


class RequirementRead(RequirementBase):
    """要件読み取り時に返すschema。"""

    id: int
    document_id: int
    version: int
    created_by: int | None = None
    updated_by: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RequirementListResponse(BaseModel):
    """要件一覧レスポンスschema。"""

    items: list[RequirementRead]
    total: int
    page: int
    page_size: int


class RequirementOpenIssueBase(BaseModel):
    """未決事項schemaの共通フィールドを定義する基底schema。"""

    related_requirement_id: int | None = None
    issue_code: str
    title: str
    description: str | None = None
    impact_scope: str | None = None
    assignee_id: int | None = None
    due_date: date | None = None
    status: str = "open"
    resolution: str | None = None


class RequirementOpenIssueCreate(RequirementOpenIssueBase):
    """未決事項作成リクエストで受け取るschema。"""

    document_id: int


class RequirementOpenIssueUpdate(BaseModel):
    """未決事項更新リクエストで受け取るschema。"""

    version: int
    related_requirement_id: int | None = None
    issue_code: str | None = None
    title: str | None = None
    description: str | None = None
    impact_scope: str | None = None
    assignee_id: int | None = None
    due_date: date | None = None
    status: str | None = None
    resolution: str | None = None
    reason: str | None = None


class RequirementOpenIssuePromoteCreate(BaseModel):
    """未決事項を要件へ昇格するリクエストで受け取るschema。"""

    version: int
    requirement_code: str
    requirement_type: str = "functional"
    section_id: int | None = None
    category: str | None = None
    title: str | None = None
    description: str | None = None
    rationale: str | None = None
    acceptance_criteria: str | None = None
    priority: str = "must"
    status: str = "draft"
    source: str | None = None
    owner_id: int | None = None
    resolution: str | None = None
    reason: str | None = None


class RequirementOpenIssueRead(RequirementOpenIssueBase):
    """未決事項読み取り時に返すschema。"""

    id: int
    document_id: int
    version: int
    created_by: int | None = None
    updated_by: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RequirementOpenIssueListResponse(BaseModel):
    """未決事項一覧レスポンスschema。"""

    items: list[RequirementOpenIssueRead]
    total: int
    page: int
    page_size: int


class RequirementChangeLogRead(BaseModel):
    """要件定義変更履歴読み取り時に返すschema。"""

    id: int
    document_id: int | None = None
    target_type: str
    target_id: int
    action: str
    field_name: str | None = None
    old_value: dict | None = None
    new_value: dict | None = None
    reason: str | None = None
    changed_by: int | None = None
    changed_at: datetime

    model_config = {"from_attributes": True}


class RequirementChangeLogListResponse(BaseModel):
    """要件定義変更履歴一覧レスポンスschema。"""

    items: list[RequirementChangeLogRead]
    total: int
    page: int
    page_size: int


class RequirementApprovalRequestCreate(BaseModel):
    """要件定義承認申請リクエストで受け取るschema。"""

    target_type: str
    target_id: int
    approver_id: int
    comment: str | None = None


class RequirementApprovalDecisionCreate(BaseModel):
    """要件定義承認判断リクエストで受け取るschema。"""

    comment: str | None = None


class RequirementApprovalRead(BaseModel):
    """要件定義承認読み取り時に返すschema。"""

    id: int
    document_id: int
    target_type: str
    target_id: int
    status: str
    approver_id: int
    requested_by: int
    requested_at: datetime
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    comment: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RequirementApprovalListResponse(BaseModel):
    """要件定義承認一覧レスポンスschema。"""

    items: list[RequirementApprovalRead]
    total: int
    page: int
    page_size: int


class RequirementTargetCommentCreate(BaseModel):
    """要件定義対象コメント作成リクエストで受け取るschema。"""

    target_type: str
    target_id: int
    parent_comment_id: int | None = None
    body: str


class RequirementTargetCommentUpdate(BaseModel):
    """要件定義対象コメント更新リクエストで受け取るschema。"""

    version: int
    body: str
    reason: str | None = None


class RequirementTargetCommentStateUpdate(BaseModel):
    """要件定義対象コメント状態更新リクエストで受け取るschema。"""

    version: int
    reason: str | None = None


class RequirementTargetCommentRead(BaseModel):
    """要件定義対象コメント読み取り時に返すschema。"""

    id: int
    document_id: int
    target_type: str
    target_id: int
    parent_comment_id: int | None = None
    body: str
    author_id: int
    is_resolved: bool
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RequirementRevisionRead(BaseModel):
    """要件改訂履歴読み取り時に返すschema。"""

    id: int
    requirement_id: int
    version: int
    changed_by: int | None = None
    change_summary: str | None = None
    before_value: dict | None = None
    after_value: dict | None = None
    reason: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RequirementDetailCreate(BaseModel):
    """要件詳細作成リクエストで受け取るschema。"""

    detail_type: str
    detail_json: dict


class RequirementDetailUpdate(BaseModel):
    """要件詳細更新リクエストで受け取るschema。"""

    detail_type: str | None = None
    detail_json: dict | None = None


class RequirementDetailRead(BaseModel):
    """要件詳細読み取り時に返すschema。"""

    id: int
    requirement_id: int
    detail_type: str
    detail_json: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RequirementLinkCreate(BaseModel):
    """要件リンク作成リクエストで受け取るschema。"""

    linked_type: str
    linked_id: str


class RequirementLinkRead(BaseModel):
    """要件リンク読み取り時に返すschema。"""

    id: int
    requirement_id: int
    linked_type: str
    linked_id: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RequirementRelationCreate(BaseModel):
    """要件関連作成リクエストで受け取るschema。"""

    target_type: str
    target_id: str
    relation_type: str
    description: str | None = None


class RequirementRelationRead(BaseModel):
    """要件関連読み取り時に返すschema。"""

    id: int
    document_id: int
    source_requirement_id: int
    target_type: str
    target_id: str
    relation_type: str
    description: str | None = None
    created_by: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RequirementCommentCreate(BaseModel):
    """要件コメント作成リクエストで受け取るschema。"""

    comment: str


class RequirementCommentRead(BaseModel):
    """要件コメント読み取り時に返すschema。"""

    id: int
    requirement_id: int
    user_id: int
    comment: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RequirementReviewCreate(BaseModel):
    """要件レビュー作成リクエストで受け取るschema。"""

    reviewer_id: int
    status: str = "pending"
    comment: str | None = None
    reviewed_at: datetime | None = None


class RequirementReviewUpdate(BaseModel):
    """要件レビュー更新リクエストで受け取るschema。"""

    reviewer_id: int | None = None
    status: str | None = None
    comment: str | None = None
    reviewed_at: datetime | None = None


class RequirementReviewRead(BaseModel):
    """要件レビュー読み取り時に返すschema。"""

    id: int
    requirement_id: int
    reviewer_id: int
    status: str
    comment: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RequirementSummaryRead(BaseModel):
    """要件詳細画面用の集約レスポンスschema。"""

    requirement: RequirementRead
    details: list[RequirementDetailRead]
    links: list[RequirementLinkRead]
    comments: list[RequirementCommentRead]
    reviews: list[RequirementReviewRead]
    revisions: list[RequirementRevisionRead]
