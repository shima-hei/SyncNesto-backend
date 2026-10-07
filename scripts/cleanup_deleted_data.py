"""期限後のごみ箱を、指定した通常組織内で確認・回収する。"""

import argparse
import logging
from datetime import UTC, datetime

from app.core.config import settings
from app.db.session import session_local
from app.services.trash import TrashService

logger = logging.getLogger(__name__)


def cleanup_deleted_data(
    *, tenant_id: int, limit: int = 20, execute: bool = False
) -> int:
    """標準はdry-run。例外時は行を残して次回再試行する。"""
    if tenant_id < 1 or not 1 <= limit <= 100:
        raise ValueError(
            "tenant-id must be positive and limit must be between 1 and 100"
        )
    if settings.app_env == "demo":
        raise ValueError("Demo data must use the session cleanup, not trash retention")
    if not 0 <= settings.deleted_data_retention_days <= 3650:
        raise ValueError("DELETED_DATA_RETENTION_DAYS must be between 0 and 3650")
    service = TrashService()
    now = datetime.now(UTC)
    with session_local() as db:
        db.info["tenant_id"] = tenant_id
        targets = service.due(db, tenant_id, limit, now)
        if not execute:
            for target in targets:
                logger.info(
                    "Dry-run: tenant=%s project=%s kind=%s id=%s deleted_at=%s",
                    tenant_id,
                    target["project_id"],
                    target["kind"],
                    target["id"],
                    target["deleted_at"],
                )
            logger.info("Dry-run: %s candidates (limit=%s)", len(targets), limit)
            return len(targets)
        count = 0
        for target in targets:
            try:
                count += service.purge_one(db, target, now)
            except Exception:
                db.rollback()
                # DB/S3例外は鍵や接続情報を含み得るため、その本文をログに出さない。
                logger.error(
                    "Purge failed: project=%s kind=%s id=%s; retry required",
                    target["project_id"],
                    target["kind"],
                    target["id"],
                )
                raise RuntimeError(
                    "Purge failed; expired rows remain for retry"
                ) from None
        logger.info("Purged %s resources in tenant=%s", count, tenant_id)
        return count


def main() -> None:
    """明示的な組織指定とexecute flagだけで実行する。"""
    parser = argparse.ArgumentParser(
        description="Cleanup expired deleted business data"
    )
    parser.add_argument("--tenant-id", required=True, type=int)
    parser.add_argument("--limit", default=20, type=int)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    cleanup_deleted_data(
        tenant_id=args.tenant_id, limit=args.limit, execute=args.execute
    )


if __name__ == "__main__":
    main()
