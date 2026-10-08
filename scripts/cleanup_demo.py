"""デモ受付停止後も、専用DBの失効済みデータを確認・回収する。"""

import argparse
import json
from datetime import UTC, datetime

from app.core.config import settings
from app.db.session import demo_session_local
from app.repositories.demo import DemoRepository
from app.schemas.demo import DemoCleanupResult
from app.services.demo import DemoService


def cleanup_demo(*, execute: bool = False) -> DemoCleanupResult:
    """標準は件数確認。実行時も既存の失効・所有範囲・再試行を維持する。"""
    if not settings.demo_data_isolated:
        raise ValueError("Demo cleanup requires a verified dedicated database")
    if execute:
        return DemoService().sweep()
    with demo_session_local() as db:
        count = len(DemoRepository().due_ids(db, datetime.now(UTC)))
    return DemoCleanupResult(processed=0, pending=count)


def main() -> int:
    """秘密や対象の本文を出さず、最大10件を一回分として扱う。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--json", action="store_true", help="機密情報を含まない集計だけを返す"
    )
    args = parser.parse_args()
    try:
        result = cleanup_demo(execute=args.execute)
        with demo_session_local() as db:
            remaining = DemoRepository().unfinished_count(db)
    except Exception:
        print("Demo cleanup failed; pending data remains for retry.")
        return 1
    if args.json:
        print(
            json.dumps(
                result.model_dump()
                | {"execute": args.execute, "limit": 10, "remaining": remaining}
            )
        )
    else:
        print(
            f"Demo cleanup: execute={args.execute}, "
            f"processed={result.processed}, pending={result.pending}, "
            f"remaining={remaining} (limit=10)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
