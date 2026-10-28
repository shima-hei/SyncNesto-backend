"""公開MCPの既存Task入力契約。業務APIの追加項目を公開しない。"""

from datetime import date

from pydantic import BaseModel, Field


class TaskBase(BaseModel):
    """タスクschemaの共通フィールドを定義する基底schema。"""

    parent_task_id: int | None = None
    task_code: str
    title: str
    description: str | None = None
    task_type: str = "other"
    status: str = "backlog"
    priority: str = "medium"
    assignee_id: int | None = None
    reporter_id: int | None = None
    start_date: date | None = None
    due_date: date | None = None
    actual_start_date: date | None = None
    actual_end_date: date | None = None
    progress_percent: int = Field(default=0, ge=0, le=100)
    estimated_minutes: int | None = Field(default=None, ge=0)
    actual_minutes: int | None = Field(default=None, ge=0)
    sort_order: int = 0
    tags: list[str] = Field(default_factory=list)


class TaskCreate(TaskBase):
    """タスク作成リクエストで受け取るschema。"""

    task_code: str | None = None
    requirement_id: int | None = None
    relation_type: str = "implements"


class TaskUpdate(BaseModel):
    """タスク更新リクエストで受け取るschema。"""

    version: int
    parent_task_id: int | None = None
    task_code: str | None = None
    title: str | None = None
    description: str | None = None
    task_type: str | None = None
    status: str | None = None
    priority: str | None = None
    assignee_id: int | None = None
    reporter_id: int | None = None
    start_date: date | None = None
    due_date: date | None = None
    actual_start_date: date | None = None
    actual_end_date: date | None = None
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    estimated_minutes: int | None = Field(default=None, ge=0)
    actual_minutes: int | None = Field(default=None, ge=0)
    sort_order: int | None = None
    tags: list[str] | None = None
    change_reason: str | None = None
