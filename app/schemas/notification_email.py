"""宛先や他人のIDを入力させない本人設定。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class NotificationEmailPreferences(BaseModel):
    """現在の宛先と結び付いた実効設定。"""

    fixed_approval_email_enabled: bool
    version: int
    available: bool
    pause_reason: Literal["identity_changed"] | None = None


class NotificationEmailPreferencesUpdate(BaseModel):
    """同じ本人の設定にだけ楽観的排他を適用する。"""

    model_config = ConfigDict(extra="forbid")
    fixed_approval_email_enabled: StrictBool
    version: int = Field(ge=0, strict=True)
