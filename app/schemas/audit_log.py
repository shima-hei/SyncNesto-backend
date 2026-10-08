"""組織管理者向け監査ログの安全な閲覧契約。"""

from datetime import datetime

from pydantic import BaseModel


class AuditLogRead(BaseModel):
    """本文・接続情報を含めない監査記録。"""

    id: int
    created_at: datetime
    event_type: str
    actor_user_id: int | None
    actor_name: str | None
    project_id: int | None
    project_name: str | None
    resource_type: str | None
    resource_id: int | None
    source: str
    details: dict[str, str | int | list[str] | list[int]]


class AuditLogListRead(BaseModel):
    """新しい記録から順に取得する一覧。"""

    items: list[AuditLogRead]
    total: int
    page: int
    page_size: int
