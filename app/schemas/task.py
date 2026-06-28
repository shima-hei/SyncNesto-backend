"""タスク管理schemaを定義するモジュール。"""

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.change_log import (
    ChangeLogUserRead,
    ChangeLogValue,
    TaskChangeLogActionCode,
    TaskChangeLogFieldName,
    TaskChangeLogTargetTypeCode,
)


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


class TaskRequirementSummary(BaseModel):
    """タスクに紐づく要件の概要を返すschema。"""

    id: int
    requirement_code: str
    title: str
    relation_id: int
    relation_type: str


class TaskRead(TaskBase):
    """タスク読み取り時に返すschema。"""

    id: int
    project_id: int
    version: int
    created_by: int | None = None
    updated_by: int | None = None
    created_at: datetime
    updated_at: datetime
    is_overdue: bool = False
    is_blocked: bool = False
    requirements: list[TaskRequirementSummary] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    """タスク一覧レスポンスschema。"""

    items: list[TaskRead]
    total: int
    page: int
    page_size: int


class TaskTagListResponse(BaseModel):
    """プロジェクト内タスクタグ候補一覧レスポンスschema。"""

    items: list[str]


class TaskCommentCreate(BaseModel):
    """タスクコメント作成リクエストで受け取るschema。"""

    parent_comment_id: int | None = None
    body: str


class TaskCommentUpdate(BaseModel):
    """タスクコメント更新リクエストで受け取るschema。"""

    version: int
    body: str


class TaskCommentStateUpdate(BaseModel):
    """タスクコメント状態更新リクエストで受け取るschema。"""

    version: int


class TaskCommentRead(BaseModel):
    """タスクコメント読み取り時に返すschema。"""

    id: int
    task_id: int
    parent_comment_id: int | None = None
    body: str
    is_resolved: bool
    version: int
    created_by: int | None = None
    created_by_user: ChangeLogUserRead | None = None
    updated_by: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskChangeLogRead(BaseModel):
    """タスク変更履歴読み取り時に返すschema。"""

    id: int
    task_id: int
    target_type: TaskChangeLogTargetTypeCode = "task"
    action: TaskChangeLogActionCode
    field_name: TaskChangeLogFieldName | None = None
    old_value: ChangeLogValue = None
    new_value: ChangeLogValue = None
    reason: str | None = None
    created_by: int | None = None
    created_by_user: ChangeLogUserRead | None = None
    created_at: datetime


class TaskChangeLogListResponse(BaseModel):
    """タスク変更履歴一覧レスポンスschema。"""

    items: list[TaskChangeLogRead]
    total: int
    page: int
    page_size: int


class RequirementTaskRelationCreate(BaseModel):
    """要件タスク関連作成リクエストで受け取るschema。"""

    task_id: int
    relation_type: str = "implements"


class RequirementTaskCreate(TaskCreate):
    """要件配下からタスクを作成するリクエストschema。"""


class RequirementTaskRelationRead(BaseModel):
    """要件タスク関連読み取り時に返すschema。"""

    id: int
    requirement_id: int
    task_id: int
    relation_type: str
    created_by: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RequirementTaskProgressRead(BaseModel):
    """要件に紐づくタスク進捗読み取りschema。"""

    requirement_id: int
    task_count: int
    progress_percent: int
    status: str


class TaskDependencyCreate(BaseModel):
    """タスク依存関係作成リクエストで受け取るschema。"""

    predecessor_task_id: int
    successor_task_id: int
    dependency_type: str = "finish_to_start"
    lag_days: int = 0


class TaskDependencyUpdate(BaseModel):
    """タスク依存関係更新リクエストで受け取るschema。"""

    version: int
    dependency_type: str | None = None
    lag_days: int | None = None


class TaskDependencyRead(BaseModel):
    """タスク依存関係読み取り時に返すschema。"""

    id: int
    predecessor_task_id: int
    successor_task_id: int
    dependency_type: str
    lag_days: int
    version: int
    created_by: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MilestoneBase(BaseModel):
    """マイルストーンschemaの共通フィールドを定義する基底schema。"""

    title: str
    description: str | None = None
    target_date: date
    status: str = "planned"


class MilestoneCreate(MilestoneBase):
    """マイルストーン作成リクエストで受け取るschema。"""


class MilestoneUpdate(BaseModel):
    """マイルストーン更新リクエストで受け取るschema。"""

    version: int
    title: str | None = None
    description: str | None = None
    target_date: date | None = None
    status: str | None = None


class MilestoneRead(MilestoneBase):
    """マイルストーン読み取り時に返すschema。"""

    id: int
    project_id: int
    version: int
    created_by: int | None = None
    updated_by: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BoardBase(BaseModel):
    """ボードschemaの共通フィールドを定義する基底schema。"""

    name: str
    description: str | None = None
    board_type: str = "kanban"


class BoardCreate(BoardBase):
    """ボード作成リクエストで受け取るschema。"""


class BoardUpdate(BaseModel):
    """ボード更新リクエストで受け取るschema。"""

    version: int
    name: str | None = None
    description: str | None = None
    board_type: str | None = None


class BoardRead(BoardBase):
    """ボード読み取り時に返すschema。"""

    id: int
    project_id: int
    version: int
    created_by: int | None = None
    updated_by: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BoardColumnBase(BaseModel):
    """ボード列schemaの共通フィールドを定義する基底schema。"""

    name: str
    status_key: str
    sort_order: int = 0
    wip_limit: int | None = Field(default=None, ge=0)
    is_done_column: bool = False


class BoardColumnCreate(BoardColumnBase):
    """ボード列作成リクエストで受け取るschema。"""


class BoardColumnUpdate(BaseModel):
    """ボード列更新リクエストで受け取るschema。"""

    version: int
    name: str | None = None
    status_key: str | None = None
    sort_order: int | None = None
    wip_limit: int | None = Field(default=None, ge=0)
    is_done_column: bool | None = None


class BoardColumnRead(BoardColumnBase):
    """ボード列読み取り時に返すschema。"""

    id: int
    board_id: int
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskMoveRequest(BaseModel):
    """ボード上のタスク移動リクエストで受け取るschema。"""

    status: str
    sort_order: int
    version: int


class GanttResponse(BaseModel):
    """ガントチャート取得レスポンスschema。"""

    tasks: list[TaskRead]
    dependencies: list[TaskDependencyRead]
    milestones: list[MilestoneRead]
