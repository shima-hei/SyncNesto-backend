"""MCP接続のブラウザ向け契約。トークンはブラウザへ返さない。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class McpProjectChoice(BaseModel):
    """本人がMCP接続を許可できるProject。"""

    id: int
    name: str
    tenant_id: int
    tenant_name: str


class McpConsentRead(BaseModel):
    """要求された操作と現在選択可能なProject。"""

    request_id: UUID
    client_name: str
    redirect_uri: str
    scopes: list[str]
    projects: list[McpProjectChoice]
    expires_at: datetime


class McpConsentCreate(BaseModel):
    """同一組織のProjectに対する明示的な同意。"""

    model_config = ConfigDict(extra="forbid")
    project_ids: list[int] = Field(min_length=1, max_length=20)


class McpRedirectRead(BaseModel):
    """同意したclientに登録済みのcallback。"""

    redirect_url: str


class McpAvailabilityRead(BaseModel):
    """本人が少なくとも一つのProjectへ接続できるか。"""

    can_connect: bool


class McpConnectionRead(BaseModel):
    """本人が取り消せる接続の公開情報。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: int
    project_ids: list[int]
    scopes: list[str]
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime
