"""ガントの表示順変更。"""

from typing import Literal

from pydantic import BaseModel, Field


class GanttOrderMove(BaseModel):
    item_id: int = Field(gt=0)
    anchor_id: int = Field(gt=0)
    placement: Literal["before", "after"]
