"""標準/独自/旧種別の選択と管理API。"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TaskTypeCreate(BaseModel):
    """内部キーはサーバーで採番し、名前だけ受け取る。"""

    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        """空白だけの種別を拒否する。"""
        value = value.strip()
        if not value:
            raise ValueError("種別名を入力してください")
        return value


class TaskTypeUpdate(TaskTypeCreate):
    """現在版を指定して名前と表示状態を保存する。"""

    version: int = Field(ge=1)
    is_active: bool


class TaskTypeRead(BaseModel):
    """旧キーも表示でき、標準/旧種別は管理対象としない。"""

    key: str
    name: str
    source: Literal["standard", "custom", "legacy"]
    is_active: bool = True
    version: int | None = None


class TaskTypeList(BaseModel):
    """案件内の種別カタログ。"""

    items: list[TaskTypeRead]
