"""ゲスト概要と社内側の版付き設定。"""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GuestProjectOverview(BaseModel):
    """社内の案件属性を参照せずに設定する公開項目。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    starts_on: date | None = None
    ends_on: date | None = None
    contact: str | None = Field(default=None, max_length=255)
    announcement: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_dates(self):
        """終了日だけが開始日以前にならないよう検証する。"""
        if self.starts_on and self.ends_on and self.starts_on > self.ends_on:
            raise ValueError("公開用の終了日は開始日以降を指定してください")
        return self


class GuestProjectProfileSettings(GuestProjectOverview):
    """社内の設定画面にだけ楽観的排他の版を返す。"""

    version: int = Field(ge=0)


class GuestProjectRead(GuestProjectOverview):
    """同じ本人が持つ有効な参加世代だけで案件を識別する。"""

    participation_id: UUID
    expires_at: datetime


class GuestProjectList(BaseModel):
    """本人が現在参加中の案件。"""

    items: list[GuestProjectRead]
    total: int
    page: int
    page_size: int
