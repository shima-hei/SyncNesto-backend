"""既存の耐久キューを、応答後と日次回収で件数・時間を制限して処理する。"""

import logging
import time
from collections import Counter

from app.core.config import settings
from app.db.session import engine
from app.services.notification_email_delivery import ApprovalMailWorker

logger = logging.getLogger(__name__)


def dispatch_pending_mail(*, limit=20, budget_seconds=25):
    """本文や宛先を出さず、結果不明の配送を自動再送しない。"""
    if not settings.is_public_environment or settings.demo_mode:
        return {"idle": 1}
    worker = ApprovalMailWorker(engine)
    counts = Counter()
    deadline = time.monotonic() + budget_seconds
    for _ in range(limit):
        if time.monotonic() >= deadline:
            break
        try:
            result = worker.run_once()
        except Exception:
            logger.error("Approval mail dispatch failed; durable queue retained")
            counts["failed"] += 1
            break
        counts.update(result)
        if "idle" in result or "busy" in result:
            break
    return dict(counts)
