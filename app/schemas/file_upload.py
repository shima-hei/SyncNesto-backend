"""環境ごとに切り替えるファイル送信のAPI契約。"""

from typing import Literal

from pydantic import BaseModel, Field


class FileUploadRequest(BaseModel):
    """送信前に確認するファイル情報。"""

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=100)
    byte_size: int = Field(gt=0, le=20 * 1024 * 1024)


class FileUploadPlan(BaseModel):
    """サーバー経由または短期URLによる直接送信の指示。"""

    mode: Literal["server", "presigned"]
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    upload_token: str | None = None
    expires_in: int | None = None


class FileUploadComplete(BaseModel):
    """送信許可に紐付く一時ファイルの登録要求。"""

    upload_token: str = Field(min_length=1, max_length=4096)
