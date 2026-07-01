"""下書き保存API schemaを定義するモジュール。"""

from datetime import datetime

from pydantic import BaseModel


class DraftUpsert(BaseModel):
    """下書き作成・更新リクエストschema。"""

    resource_type: str
    resource_id: int | None = None
    project_id: int | None = None
    schema_version: int = 1
    content: dict
    version: int | None = None


class DraftRead(BaseModel):
    """下書き読み取りschema。"""

    id: int
    owner_user_id: int
    scope_key: str
    resource_type: str
    resource_id: int | None = None
    project_id: int | None = None
    schema_version: int
    content: dict
    version: int
    updated_at: datetime
    expires_at: datetime

    model_config = {"from_attributes": True}


class DraftListResponse(BaseModel):
    """下書き一覧レスポンスschema。"""

    items: list[DraftRead]
    total: int
