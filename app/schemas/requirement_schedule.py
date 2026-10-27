"""正本を複製せず、取得量を制限した日程参照。"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class ScheduleTask(BaseModel):
    """日程表示に必要なTaskの最小属性。"""

    id: int
    task_code: str
    title: str
    start_date: date | None
    due_date: date | None
    status: str
    progress_percent: int


class ScheduleGroup(BaseModel):
    """同じTaskを参照できる要件行。"""

    id: int
    document_id: int
    requirement_code: str
    title: str
    task_count: int
    tasks: list[ScheduleTask]
    has_more: bool


class ScheduleMilestone(BaseModel):
    """本文や編集権限を含まない案件全体の節目。"""

    id: int
    title: str
    target_date: date
    status: str


class ScheduleSummary(BaseModel):
    """表示行とは別のフィルタ全体の一意Task件数。"""

    total: int
    planned: int
    unplanned: int
    overdue: int


class RequirementSchedule(BaseModel):
    """無効時に業務データを返さない。"""

    enabled: bool
    basis_date: date | None = None
    window_start: date | None = None
    window_end: date | None = None
    page: int = 1
    total: int | None = None
    groups: list[ScheduleGroup] = Field(default_factory=list)
    summary: ScheduleSummary | None = None
    common_state: Literal["ready", "outside_requirement_filter"] = "ready"
    common_page: int = 1
    common_total: int | None = None
    common_tasks: list[ScheduleTask] = Field(default_factory=list)
    milestones: list[ScheduleMilestone] = Field(default_factory=list)
    milestones_has_more: bool = False
