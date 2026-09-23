"""要件追跡、設計コメント、実行証跡のAPI契約。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RequirementTestItemCreate(BaseModel):
    """要件と項目の関連追加。"""

    requirement_id: int = Field(gt=0)
    item_id: UUID


class RequirementTestItemRead(BaseModel):
    """双方から辿れる関連の表示情報。"""

    id: int
    requirement_id: int
    document_id: int
    requirement_code: str
    requirement_title: str
    item_id: UUID
    item_code: str
    item_content: str
    design_id: int
    design_name: str
    item_deleted: bool
    created_at: datetime


class RequirementCoverageRead(BaseModel):
    """関連テスト項目の有無を表すトレーサビリティ情報。"""

    requirement_id: int
    linked_item_count: int
    has_tests: bool


class TestDesignCommentCreate(BaseModel):
    """対象を指定した設計コメント投稿。"""

    target_type: Literal[
        "design",
        "test_item",
        "pattern_table",
        "factor",
        "factor_level",
        "combination",
        "expected_value",
    ]
    target_id: UUID | None = None
    field: str | None = Field(default=None, max_length=100)
    parent_comment_id: int | None = None
    body: str = Field(min_length=1, max_length=20000)


class TestDesignCommentUpdate(BaseModel):
    """コメント本文または状態の競合付き更新。"""

    version: int = Field(ge=1)
    body: str | None = Field(default=None, min_length=1, max_length=20000)
    is_resolved: bool | None = None


class TestDesignCommentRead(BaseModel):
    """対象状態を含むコメント表示情報。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    design_id: int
    target_type: str
    target_id: UUID | None
    field: str | None
    target_snapshot: dict
    target_status: Literal["current", "changed", "missing"]
    parent_comment_id: int | None
    body: str
    author_id: int
    author_name: str | None
    is_resolved: bool
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class TestDesignCommentChangeRead(BaseModel):
    """コメント変更履歴。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    comment_id: int
    actor_id: int
    action: str
    old_value: dict | None
    new_value: dict | None
    created_at: datetime


class TestExecutionRead(BaseModel):
    """一回のテスト実行履歴。"""

    id: UUID
    case_id: UUID
    run_number: int
    status: str
    actual_result: str
    notes: str
    source: dict
    executed_by: int | None
    executed_by_name: str | None
    executed_at: datetime
    evidence_count: int


class TestEvidenceRead(BaseModel):
    """実行証跡のメタデータ。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    execution_id: UUID
    filename: str
    content_type: str
    byte_size: int
    uploaded_by: int
    uploaded_at: datetime


class TestEvidenceDownload(BaseModel):
    """短期有効な閲覧URL。"""

    url: str
