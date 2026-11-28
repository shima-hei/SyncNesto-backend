"""画面・API・DBの設計を扱うMCP入力。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.design_content import ArtifactKind, json_size, validate_document_tree
from app.schemas.design_workspace import (
    CollectionInput,
    CollectionKind,
    DesignImpactUpdate,
)
from app.schemas.mcp_tools import PageInput, ToolInput, WriteInput
from app.schemas.specification import SpecificationCreate, SpecificationUpdate


class DesignListInput(PageInput):
    """設計本文を読み出さず、案件内の設計を検索する。"""

    kind: ArtifactKind | None = None
    q: str = Field(default="", max_length=200)


class DesignDiagramInput(PageInput):
    """設計図の対象を指定する。"""

    kind: ArtifactKind
    q: str = Field(default="", max_length=200)
    group_id: UUID | None = None
    collection_id: UUID | None = None
    include_references: bool = False


class DesignArtifactInput(ToolInput):
    """指定した設計・版の必要部分を取得する。"""

    design_id: int = Field(gt=0)
    revision: int | None = Field(default=None, gt=0)
    section: Literal["summary", "structure", "mockups", "openapi", "ddl"] = "summary"


class DesignArtifactCreate(WriteInput):
    """設計の下書き作成。"""

    data: SpecificationCreate


class DesignArtifactUpdate(WriteInput):
    """取得した版を指定した下書きの更新。"""

    design_id: int = Field(gt=0)
    data: SpecificationUpdate


DesignItemSection = Literal[
    "fields",
    "actions",
    "states",
    "transitions",
    "artboards",
    "components",
    "mockups",
    "request_fields",
    "response_fields",
    "responses",
    "processing_steps",
    "columns",
    "constraints",
    "indexes",
    "relationships",
    "mappings",
]


class DesignItemsInput(PageInput):
    """指定した構成項目だけを下書きまたは固定版からページ取得する。"""

    design_id: int = Field(gt=0)
    revision: int | None = Field(default=None, gt=0)
    section: DesignItemSection
    parent_id: UUID | None = None
    item_ids: set[UUID] | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def validate_parent(self):
        if (self.section == "components") != (self.parent_id is not None):
            raise ValueError("componentsだけにアートボードIDを指定してください")
        return self


class DesignItemEdit(BaseModel):
    """レコードのUUIDを保持し、項目単位で追加・更新・削除する。"""

    model_config = ConfigDict(extra="forbid")
    section: DesignItemSection
    parent_id: UUID | None = None
    item_id: UUID
    mode: Literal["add", "update", "remove"]
    values: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_edit(self):
        if (self.section == "components") != (self.parent_id is not None):
            raise ValueError("componentsだけにアートボードIDを指定してください")
        if "id" in self.values or (self.mode == "remove" and self.values):
            raise ValueError("項目IDは変更できません。削除には値を指定しないでください")
        return self


class DesignArtifactPatch(BaseModel):
    """設計の版を指定して、必要なフィールドだけを差し替える。"""

    model_config = ConfigDict(extra="forbid")
    design_id: int = Field(gt=0)
    version: int = Field(gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=10000)
    settings: dict = Field(default_factory=dict)
    items: list[DesignItemEdit] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def validate_edits(self):
        if not (
            self.title is not None
            or self.summary is not None
            or self.settings
            or self.items
        ):
            raise ValueError("変更内容を指定してください")
        if "type" in self.settings:
            raise ValueError("設計種別を変更できません")
        if {item.section for item in self.items} & self.settings.keys() or (
            "artboards" in self.settings
            and any(item.section == "components" for item in self.items)
        ):
            raise ValueError("同じ構成を一括置換と項目操作で同時に指定できません")
        return self


class DesignArtifactPatchBatch(WriteInput):
    """複数設計の差分を1トランザクションで下書きへ保存する。"""

    updates: list[DesignArtifactPatch] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_batch(self):
        if len({item.design_id for item in self.updates}) != len(self.updates):
            raise ValueError("一括更新に同じ設計を複数指定できません")
        data = self.model_dump(mode="json")
        validate_document_tree(data)
        if json_size(data) > 600_000:
            raise ValueError("一括更新は600KB以内にしてください")
        return self


class DesignLinksInput(PageInput):
    design_id: int = Field(gt=0)


class DesignImpactInput(DesignLinksInput):
    base_version: int | None = Field(default=None, gt=0)


class DesignImpactWrite(WriteInput):
    design_id: int = Field(gt=0)
    data: DesignImpactUpdate


class DesignCollectionInput(ToolInput):
    collection_id: UUID
    kind: CollectionKind


class DesignCollectionCreate(WriteInput):
    data: CollectionInput


class DesignCollectionUpdate(DesignCollectionCreate):
    collection_id: UUID


class DesignReviewListInput(ToolInput):
    pending_only: bool = True
