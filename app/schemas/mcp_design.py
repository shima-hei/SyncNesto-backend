"""画面・API・DBの設計を扱うMCP入力。"""

from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.design_content import ArtifactKind
from app.schemas.mcp_tools import PageInput, ToolInput, WriteInput
from app.schemas.specification import SpecificationCreate, SpecificationUpdate


class DesignListInput(PageInput):
    """設計本文を読み出さず、案件内の設計を検索する。"""

    kind: ArtifactKind | None = None
    q: str = Field(default="", max_length=200)


class DesignDiagramInput(DesignListInput):
    """設計図の対象を指定する。"""

    kind: ArtifactKind
    group_id: UUID | None = None


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
