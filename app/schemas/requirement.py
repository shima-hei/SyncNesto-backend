"""要件定義schemaを定義するモジュール。"""

from datetime import datetime

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


class RequirementBase(BaseModel):
    """要件schemaの共通フィールドを定義する基底schema。"""

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
