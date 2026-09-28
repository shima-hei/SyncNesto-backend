"""テストケースと不具合タスクの関連および進捗集計API。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TestCaseIssueCreate(BaseModel):
    """既存不具合タスクとの関連を作る。"""

    task_id: int = Field(gt=0)
    origin_execution_id: UUID | None = None


class TestCaseIssueRead(BaseModel):
    """ケース側から表示する不具合。"""

    id: int
    case_id: UUID
    task_id: int
    task_code: str
    title: str
    status: str
    origin_execution_id: UUID | None
    created_at: datetime


class IssueTestCaseRead(BaseModel):
    """不具合側から表示する関連ケース。"""

    link_id: int
    case_id: UUID
    design_id: int
    design_name: str
    item_code: str
    item_content: str
    pattern_code: str | None
    status: str
    origin_execution_id: UUID | None


class IssueSummaryRead(BaseModel):
    """設計書内の関連不具合とケース数。"""

    task_id: int
    task_code: str
    title: str
    status: str
    case_count: int
    failed_case_count: int


class TestProgressRead(BaseModel):
    """最新ケース状態だけを用いた集計。"""

    design_id: int
    target_feature: str | None
    total: int
    not_run: int
    in_progress: int
    passed: int
    failed: int
    blocked: int
    not_applicable: int
    progress_numerator: int
    progress_denominator: int
    ng_numerator: int
    ng_denominator: int
    issue_count: int
    failed_without_issue: int
    issues: list[IssueSummaryRead]
