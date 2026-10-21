"""案件の共通出典と文書に保存する任意の目標。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ObjectiveFields(BaseModel):
    """既存目的とは別の、初期入力を要求しない3項目。"""

    project_goal: str | None = Field(default=None, max_length=20000)
    project_scope: str | None = Field(default=None, max_length=20000)
    project_completion_criteria: str | None = Field(default=None, max_length=20000)

    @field_validator("project_goal", "project_scope", "project_completion_criteria")
    @classmethod
    def blank_as_none(cls, value: str | None) -> str | None:
        """非空の本文の空白やMarkdownは変えない。"""
        return value if value and value.strip() else None


class ObjectiveDocument(ObjectiveFields):
    """現在参照できる出典。固定版ではない。"""

    id: int
    document_code: str
    title: str
    version: int
    model_config = ConfigDict(from_attributes=True)


class ProjectObjectiveRead(BaseModel):
    """閲覧権限がないときは参照の存在も返さない。"""

    enabled: bool
    state: Literal["disabled", "restricted", "unselected", "available", "unavailable"]
    version: int | None = None
    document: ObjectiveDocument | None = None
    can_manage_source: bool = False
    can_edit_document: bool = False
    can_create_document: bool = False


class ProjectObjectiveUpdate(BaseModel):
    """本文を受け取らず、参照と確認した版だけを更新する。"""

    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    document_id: int | None = Field(ge=1)
    expected_document_version: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_document_version(self):
        """選択は文書版を必須とし、解除時は文書版を受け取らない。"""
        if (self.document_id is None) != (self.expected_document_version is None):
            raise ValueError("選択する文書と確認した版を指定してください")
        return self


class GoalSourceEventRead(BaseModel):
    """本文を含まない選択履歴。名称は現在参照できるものだけ。"""

    id: int
    previous_document_id: int | None
    document_id: int | None
    previous_document_title: str | None = None
    document_title: str | None = None
    previous_version: int
    version: int
    document_version: int | None
    reason: str | None
    changed_by: int
    changed_by_name: str | None
    changed_at: datetime


class GoalSourceEventPage(BaseModel):
    """認可した案件だけの履歴ページ。"""

    items: list[GoalSourceEventRead]
    total: int
    page: int
    page_size: int
