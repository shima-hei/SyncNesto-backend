"""スマートリンクはURLの代わりに正規化した成果物の識別子を受け取る。"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.search import SearchKind

SmartLinkKind = SearchKind | Literal["project", "document_attachment"]
SmartLinkGroup = Literal[
    "project", "requirement", "task", "specification", "test", "document"
]
UUID_KINDS = {"test_item", "test_case", "document_attachment"}
CONTAINER_KINDS = UUID_KINDS | {"requirement"}


class SmartLinkReference(BaseModel):
    """URL内の案件と親成果物も照合する。"""

    kind: SmartLinkKind
    id: str = Field(min_length=1, max_length=36)
    project_id: int = Field(gt=0, le=2147483647)
    container_id: int | None = Field(default=None, gt=0, le=2147483647)

    @model_validator(mode="after")
    def validate_reference(self):
        if (self.container_id is not None) != (self.kind in CONTAINER_KINDS):
            raise ValueError("The artifact container is invalid")
        if self.kind in UUID_KINDS:
            if str(UUID(self.id)) != self.id:
                raise ValueError("Use a canonical UUID")
        elif (
            not self.id.isascii()
            or not self.id.isdecimal()
            or not 0 < int(self.id) <= 2147483647
            or str(int(self.id)) != self.id
        ):
            raise ValueError("Use a canonical positive identifier")
        if self.kind == "project" and self.id != str(self.project_id):
            raise ValueError("The project identifier does not match")
        return self


class SmartLinkResolve(BaseModel):
    """同時に表示される参照をまとめて解決する。"""

    references: list[SmartLinkReference] = Field(min_length=1, max_length=40)
    preview: bool = False


class SmartLinkPerson(BaseModel):
    name: str
    avatar_url: str | None = None


class SmartLinkPreview(BaseModel):
    status: str | None = None
    priority: str | None = None
    requirement_type: str | None = None
    assignee: SmartLinkPerson | None = None
    due_date: date | None = None
    updated_at: datetime | None = None
    file_type: str | None = None
    test_item_count: int | None = None
    actual_result: str | None = None


class SmartLinkRead(SmartLinkReference):
    title: str
    code: str = ""
    project_name: str
    specification_kind: str | None = None
    preview: SmartLinkPreview | None = None
