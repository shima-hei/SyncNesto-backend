"""画面一覧の表示項目と、複数行を同じ版照合で保存する契約。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.design_content import ScreenKind
from app.schemas.specification import SpecificationStatus

ScreenListSort = Literal[
    "id", "title", "route", "screen_kind", "status", "updated_at", "assignee"
]


class ScreenListQuery(BaseModel):
    q: str = Field(default="", max_length=200)
    group_id: UUID | None = None
    screen_kind: ScreenKind | None = None
    status: SpecificationStatus | None = None
    assignee_id: int | None = Field(default=None, ge=0)
    sort: ScreenListSort = "id"
    direction: Literal["asc", "desc"] = "asc"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)


class ScreenListReference(BaseModel):
    id: int
    title: str
    code: str
    container_id: int | None = None


class ScreenListGroup(BaseModel):
    id: UUID
    name: str


class ScreenListMember(BaseModel):
    id: int
    name: str


class ScreenListRow(BaseModel):
    id: int
    title: str
    route: str
    screen_kind: ScreenKind
    status: SpecificationStatus
    version: int
    updated_at: datetime
    groups: list[ScreenListGroup]
    related_apis: list[ScreenListReference]
    related_requirements: list[ScreenListReference]
    assignee: ScreenListMember | None


class ScreenListRead(BaseModel):
    items: list[ScreenListRow]
    total: int
    page: int
    page_size: int


class ScreenListEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: int | None = Field(default=None, gt=0)
    version: int | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    route: str | None = Field(default=None, max_length=1000)
    screen_kind: ScreenKind | None = None
    assignee_id: int | None = Field(default=None, gt=0)
    group_ids: list[UUID] | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def validate_edit(self):
        if (self.id is None) != (self.version is None):
            raise ValueError("既存行の更新にはIDと版を指定してください")
        if self.id is None and self.title is None:
            raise ValueError("新しい画面の名前を指定してください")
        return self


class ScreenListBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    edits: list[ScreenListEdit] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def distinct_rows(self):
        ids = [edit.id for edit in self.edits if edit.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("同じ画面を重複して更新できません")
        return self


class ScreenListSaved(BaseModel):
    id: int
    version: int
    title: str
