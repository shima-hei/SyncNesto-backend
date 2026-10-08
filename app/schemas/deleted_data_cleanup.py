"""運用者専用の定期回収結果。本文やストレージ情報を含めない。"""

from typing import Literal

from pydantic import BaseModel


class DeletedDataCleanupResult(BaseModel):
    """候補は今回の上限内の件数であり、未回収全体の総数ではない。"""

    status: Literal["completed", "busy"] = "completed"
    mode: Literal["dry_run", "execute"]
    candidate_count: int = 0
    purged_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    has_more: bool = False
