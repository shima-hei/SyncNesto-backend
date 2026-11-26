"""コメント画像のアップロードと表示情報。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CommentImageKind = Literal["task", "requirement", "specification", "test_design"]
MAX_COMMENT_IMAGES = 4


class CommentImageCreateFields(BaseModel):
    image_ids: list[UUID] = Field(default_factory=list, max_length=MAX_COMMENT_IMAGES)


class CommentImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: int
    filename: str
    content_type: str
    byte_size: int


class CommentImageReadFields(BaseModel):
    images: list[CommentImageRead] = Field(default_factory=list)
