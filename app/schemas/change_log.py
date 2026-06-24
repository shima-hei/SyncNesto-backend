"""変更履歴APIで共通利用するschemaを定義するモジュール。"""

from typing import Literal, TypeAlias

from pydantic import BaseModel, Field

ChangeLogScalar: TypeAlias = str | int | float | bool | None
ChangeLogSnapshot: TypeAlias = dict[str, object]


class ChangeLogUserRead(BaseModel):
    """変更履歴の変更者として返す軽量ユーザーschema。"""

    id: int
    name: str
    email: str
    avatar_url: str | None = None


class ChangeLogCodeLabelValue(BaseModel):
    """enumコード値と表示補助ラベルを返すschema。"""

    code: str
    label: str


class ChangeLogKeyLabelValue(BaseModel):
    """key値と表示補助ラベルを返すschema。"""

    key: str
    label: str


class ChangeLogIdLabelValue(BaseModel):
    """ID値と表示補助ラベルを返すschema。"""

    id: int
    label: str


class ChangeLogSnapshotValue(BaseModel):
    """変更前後のスナップショットを返すschema。"""

    snapshot: ChangeLogSnapshot


class ChangeLogUpdatedFieldsValue(BaseModel):
    """複数フィールド更新時の変更項目とスナップショットを返すschema。"""

    updated_fields: list[str] = Field(default_factory=list)
    snapshot: ChangeLogSnapshot


ChangeLogValue: TypeAlias = (
    ChangeLogCodeLabelValue
    | ChangeLogKeyLabelValue
    | ChangeLogIdLabelValue
    | ChangeLogSnapshotValue
    | ChangeLogUpdatedFieldsValue
    | dict[str, object]
    | list[dict[str, object]]
    | list[object]
    | ChangeLogScalar
)

TaskChangeLogActionCode: TypeAlias = Literal[
    "created",
    "updated",
    "deleted",
    "status_changed",
    "assignee_changed",
    "schedule_changed",
    "progress_changed",
    "comment_created",
    "comment_updated",
    "comment_deleted",
    "comment_resolved",
    "comment_reopened",
]

TaskChangeLogFieldName: TypeAlias = Literal[
    "task_code",
    "title",
    "description",
    "status",
    "priority",
    "task_type",
    "assignee_id",
    "reporter_id",
    "start_date",
    "due_date",
    "actual_start_date",
    "actual_end_date",
    "estimated_minutes",
    "actual_minutes",
    "progress_percent",
    "parent_task_id",
    "sort_order",
    "tags",
    "requirements",
    "body",
    "is_resolved",
]

TaskChangeLogTargetTypeCode: TypeAlias = Literal["task", "task_comment"]

RequirementChangeLogActionCode: TypeAlias = Literal[
    "created",
    "updated",
    "deleted",
    "exported",
    "sorted",
    "promoted_to_requirement",
    "comment_created",
    "comment_updated",
    "comment_deleted",
    "comment_resolved",
    "comment_reopened",
    "approval_requested",
    "approval_approved",
    "approval_rejected",
]

RequirementChangeLogTargetTypeCode: TypeAlias = Literal[
    "requirement_document",
    "requirement_section",
    "requirement",
    "requirement_detail",
    "requirement_link",
    "requirement_relation",
    "requirement_review",
    "requirement_open_issue",
    "requirement_comment",
]

RequirementChangeLogFieldName: TypeAlias = Literal[
    "title",
    "document_code",
    "status",
    "purpose",
    "author_id",
    "reviewer_id",
    "approver_id",
    "sort_order",
    "requirement_code",
    "requirement_type",
    "category",
    "description",
    "rationale",
    "acceptance_criteria",
    "priority",
    "source",
    "owner_id",
    "issue_code",
    "assignee_id",
    "due_date",
    "body",
    "is_resolved",
]
