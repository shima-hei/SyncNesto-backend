"""デモの利用状態だけを公開するAPI契約。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DemoStatus(BaseModel):
    """キャッシュ名前空間と延長できない終了時刻。"""

    id: UUID
    tenant_id: int
    expires_at: datetime
    absolute_expires_at: datetime


class DemoCleanupResult(BaseModel):
    """機密情報を含まない定期回収結果。"""

    processed: int
    pending: int
