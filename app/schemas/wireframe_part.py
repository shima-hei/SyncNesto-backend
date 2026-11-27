"""案件で共有するワイヤーフレーム部品の入出力。"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.design_content import (
    PlacementMode,
    WireframeLayer,
    ensure_unique_design_ids,
    json_size,
    validate_wireframe_layers,
)


class WireframePartContent(BaseModel):
    """画面固有の関連を含まない部品のレイアウト。"""

    model_config = ConfigDict(extra="forbid")
    width: int = Field(ge=12, le=10000)
    height: int = Field(ge=12, le=10000)
    layout: PlacementMode = "free"
    gap: int = Field(default=8, ge=0, le=200)
    padding: int = Field(default=8, ge=0, le=200)
    columns: int = Field(default=2, ge=1, le=20)
    layers: list[WireframeLayer] = Field(min_length=1, max_length=199)

    @model_validator(mode="after")
    def validate_part(self):
        validate_wireframe_layers(self.layers)
        content = self.model_dump(mode="json")
        ensure_unique_design_ids(content)
        if json_size(content) > 250_000:
            raise ValueError("共通部品は250KB以内にしてください")
        return self


class WireframePartCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    content: WireframePartContent


class WireframePartUpdate(WireframePartCreate):
    version: int = Field(ge=1, le=2_147_483_646)


class WireframePartSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    part_id: UUID
    name: str
    version: int


class WireframePartRead(WireframePartSummary):
    content: WireframePartContent
