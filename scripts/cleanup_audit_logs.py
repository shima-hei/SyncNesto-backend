"""古い監査ログを手動削除するメンテナンスコマンド。"""

import argparse
import logging

from app.core.config import settings
from app.db.session import session_local
from app.services.audit_log import AuditLogService

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を解析する。

    Returns:
        解析済み引数。
    """
    parser = argparse.ArgumentParser(description="Cleanup old audit logs")
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=settings.audit_log_retention_days,
        help="削除対象とする経過日数。未指定時はAUDIT_LOG_RETENTION_DAYSを使う。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="最大削除件数。",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="実際に削除する。未指定時はdry-run。",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    """削除コマンドの引数を検証する。

    Args:
        args: 解析済み引数。

    Raises:
        ValueError: 引数が不正な場合。
    """
    if args.older_than_days < settings.audit_log_cleanup_min_days:
        raise ValueError(
            "older-than-days must be greater than or equal to "
            f"{settings.audit_log_cleanup_min_days}"
        )

    if args.limit is not None and args.limit <= 0:
        raise ValueError("limit must be greater than 0")


def cleanup_audit_logs(
    *,
    older_than_days: int,
    limit: int | None,
    execute: bool,
) -> int:
    """古い監査ログを削除またはdry-runする。

    Args:
        older_than_days: 削除対象とする経過日数。
        limit: 最大削除件数。
        execute: 実削除する場合はTrue。

    Returns:
        dry-run時は削除対象件数、execute時は削除件数。
    """
    service = AuditLogService()
    with session_local() as db:
        target_count = service.count_cleanup_targets(
            db,
            older_than_days=older_than_days,
        )
        if not execute:
            logger.info(
                "Dry-run: %s audit logs are older than %s days",
                target_count,
                older_than_days,
            )
            return target_count

        deleted_count = service.cleanup_old_logs(
            db,
            older_than_days=older_than_days,
            limit=limit,
        )
        logger.info(
            "Deleted %s audit logs older than %s days",
            deleted_count,
            older_than_days,
        )
        return deleted_count


def main() -> None:
    """監査ログ削除コマンドを実行する。"""
    logging.basicConfig(level=logging.INFO)
    args = parse_args()
    validate_args(args)
    cleanup_audit_logs(
        older_than_days=args.older_than_days,
        limit=args.limit,
        execute=args.execute,
    )


if __name__ == "__main__":
    main()
