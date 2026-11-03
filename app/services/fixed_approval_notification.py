"""固定版承認の通知を正本と同じtransactionで保存する。"""

from app.repositories.fixed_approval_notification import (
    FixedApprovalNotificationRepository,
)


def emit_fixed_approval(db, approval_id, action):
    """今回のイベントだけを保存し、過去履歴へ遡及しない。"""
    db.flush()
    FixedApprovalNotificationRepository().emit(db, approval_id, action)
