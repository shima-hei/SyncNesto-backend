"""プレーンテキストに添えるメンションのAPI契約。"""

from typing import Literal

from pydantic import BaseModel, Field

MentionPermission = Literal["requirement:read", "task:read", "test_plan:read"]


class CommentMentionOccurrence(BaseModel):
    """UTF-16単位の半開区間でメンションを指定する。"""

    user_id: int = Field(gt=0, strict=True)
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    display_name: str = Field(min_length=1, max_length=255)
