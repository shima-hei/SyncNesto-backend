"""プロジェクト概要の集約表示用schema。"""

from datetime import datetime

from pydantic import BaseModel


class RequirementOverview(BaseModel):
    total: int
    covered: int
    uncovered: int


class TaskOverview(BaseModel):
    total: int
    done: int
    overdue: int
    blocked: int


class TestOverview(BaseModel):
    total: int
    executed: int
    passed: int
    failed: int
    not_run: int
    failed_without_issue: int


class IssueOverview(BaseModel):
    total: int
    open: int


class OverviewAttention(BaseModel):
    kind: str
    code: str
    title: str
    state: str
    target_id: str
    document_id: int | None = None
    design_id: int | None = None


class ProjectActivity(BaseModel):
    kind: str
    action: str
    code: str
    title: str
    actor_name: str | None = None
    occurred_at: datetime
    target_id: str
    document_id: int | None = None
    design_id: int | None = None


class ProjectOverviewRead(BaseModel):
    requirements: RequirementOverview | None = None
    tasks: TaskOverview | None = None
    tests: TestOverview | None = None
    issues: IssueOverview | None = None
    attention: list[OverviewAttention]
    activities: list[ProjectActivity]


class ProjectActivityListRead(BaseModel):
    items: list[ProjectActivity]
    page: int
    page_size: int
    has_more: bool
