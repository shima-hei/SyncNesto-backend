"""固定版承認の通知を正本と同じtransactionで保存する。"""

from app.core.config import settings
from app.repositories.fixed_approval_notification import (
    FixedApprovalNotificationRepository,
)


def emit_fixed_approval(db, approval_id, action):
    """停止中・冪等再送・過去履歴へ遡及して生成しない。"""
    if settings.fixed_approval_notifications_enabled:
        db.flush()
        FixedApprovalNotificationRepository().emit(db, approval_id, action)
