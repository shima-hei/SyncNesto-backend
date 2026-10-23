"""共有添付の新規発行だけに使う入出力。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SharedFileDownloadRequest(BaseModel):
    """現在確認した本人許可の世代だけを指定する。"""

    model_config = ConfigDict(extra="forbid")
    expected_authorization_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class SharedFileDownload(BaseModel):
    """メモリ内の1操作で使う、実期限付きのGET URL。"""

    url: str = Field(repr=False)
    expires_in: int = Field(ge=1, le=60)
    expires_at: datetime
    authorization_revision: str
