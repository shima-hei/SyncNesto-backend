"""明示した通常組織だけを、有限の件数と時間で日次回収する。"""

import logging
from collections import defaultdict
from datetime import UTC, datetime
from time import monotonic

from sqlalchemy import text

from app.core.config import settings
from app.db.session import session_local
from app.schemas.deleted_data_cleanup import DeletedDataCleanupResult
from app.services.audit_log import AuditLogService
from app.services.storage import StorageService
from app.services.trash import TrashService

logger = logging.getLogger(__name__)
CLEANUP_LOCK = 731892605


class DeletedDataCleanupService:
    """Cookie権限やリクエスト指定の組織を定期ジョブへ持ち込まない。"""

    def __init__(self, storage: StorageService | None = None) -> None:
        """ストレージは通常のS3互換APIを再利用する。"""
        self.storage = storage

    def run(self) -> DeletedDataCleanupResult:
        """DBのトランザクションロックを、各対象のcommitとは別に保持する。"""
        settings.validate_cleanup()
        if settings.demo_mode or settings.deleted_data_cleanup_mode == "disabled":
            raise RuntimeError("Scheduled deleted data cleanup is disabled")
        result = DeletedDataCleanupResult(
            mode="execute"
            if settings.deleted_data_cleanup_mode == "execute"
            else "dry_run"
        )
        deadline = monotonic() + settings.deleted_data_cleanup_budget_seconds
        with session_local() as lock_db:
            if not lock_db.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": CLEANUP_LOCK}
            ):
                result.status = "busy"
                return result
            # この接続のtransactionは閉じるまで維持する。poolにsession lockを残さない。
            return self._run_locked(result, deadline)

    def _run_locked(
        self, result: DeletedDataCleanupResult, deadline: float
    ) -> DeletedDataCleanupResult:
        """古い候補を優先し、組織ごとに独立したSessionを使う。"""
        now = datetime.now(UTC)
        limit = settings.deleted_data_cleanup_limit
        service = TrashService()
        with session_local() as db:
            tenant_ids = service.repository.cleanup_tenant_ids(
                db, settings.cleanup_tenant_ids()
            )
        candidates: list[dict] = []
        for tenant_id in tenant_ids:
            if monotonic() >= deadline:
                result.has_more = True
                break
            with session_local() as db:
                db.info["tenant_id"] = tenant_id
                candidates.extend(
                    {**target, "tenant_id": tenant_id}
                    for target in service.due(db, tenant_id, limit + 1, now)
                )
        candidates.sort(
            key=lambda t: (t["deleted_at"], t["tenant_id"], t["kind"], t["id"])
        )
        result.has_more = result.has_more or len(candidates) > limit
        candidates = candidates[:limit]
        result.candidate_count = len(candidates)
        counts: dict[int, dict[str, int]] = defaultdict(
            lambda: {"count": 0, "purged_count": 0, "failed_count": 0}
        )
        if result.mode == "execute" and candidates:
            service = TrashService(
                self.storage or StorageService(request_timeout_seconds=5)
            )
        for target in candidates:
            counts[target["tenant_id"]]["count"] += 1
        for target in candidates:
            tenant_id = target["tenant_id"]
            if result.mode == "dry_run":
                logger.info(
                    "Cleanup candidate: tenant=%s project=%s kind=%s id=%s "
                    "deleted_at=%s",
                    tenant_id,
                    target["project_id"],
                    target["kind"],
                    target["id"],
                    target["deleted_at"],
                )
                continue
            if monotonic() >= deadline:
                result.has_more = True
                break
            with session_local() as db:
                db.info["tenant_id"] = tenant_id
                # 復元などとのロック競合を長時間待たず、次回に回す。
                db.execute(text("SET LOCAL lock_timeout = '1000ms'"))
                db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
                try:
                    if service.purge_one(db, target, now, deadline=deadline):
                        result.purged_count += 1
                        counts[tenant_id]["purged_count"] += 1
                    else:
                        result.skipped_count += 1
                except TimeoutError:
                    db.rollback()
                    result.has_more = True
                    break
                except Exception:
                    db.rollback()
                    result.failed_count += 1
                    counts[tenant_id]["failed_count"] += 1
                    logger.error(
                        "Cleanup failed: tenant=%s kind=%s id=%s; retry required",
                        tenant_id,
                        target["kind"],
                        target["id"],
                    )
        for tenant_id, totals in counts.items():
            with session_local() as db:
                db.info["tenant_id"] = tenant_id
                AuditLogService().record(
                    db,
                    event_type="trash.cleanup",
                    tenant_id=tenant_id,
                    resource_type="trash",
                    metadata={
                        **totals,
                        "mode": result.mode,
                        "retention_days": settings.deleted_data_retention_days,
                    },
                )
        logger.info(
            "Cleanup finished: mode=%s candidates=%s purged=%s failed=%s has_more=%s",
            result.mode,
            result.candidate_count,
            result.purged_count,
            result.failed_count,
            result.has_more,
        )
        return result
