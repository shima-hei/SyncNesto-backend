"""LOCAL承認メールの配送・安全な件数確認・根拠付き手動解決。"""

import argparse
import json
import os
import signal
import time
from pathlib import Path
from uuid import UUID

from app.db.session import engine
from app.services.notification_email_delivery import ApprovalMailWorker


def main() -> int:
    """通知先/本文/secretを標準出力へ出さない。"""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--once", action="store_true")
    commands.add_parser("status")
    resolve = commands.add_parser("resolve")
    resolve.add_argument("delivery_id", type=UUID)
    resolve.add_argument("--version", type=int, required=True)
    resolve.add_argument(
        "--outcome", choices=["accepted", "not-accepted"], required=True
    )
    resolve.add_argument("--reason", required=True)
    resolve.add_argument("--evidence-file", type=Path, required=True)
    args = parser.parse_args()
    worker = ApprovalMailWorker(engine)
    stopped = False

    def stop(signum, frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if args.command == "status":
            print(json.dumps(worker.status()))
        elif args.command == "resolve":
            path = args.evidence_file
            if (
                path.is_symlink()
                or path.stat().st_uid != os.getuid()
                or path.stat().st_mode & 0o077
                or path.stat().st_size > 8192
            ):
                raise ValueError("owner_only_evidence_file_required")
            evidence = json.loads(path.read_text())
            worker.resolve(
                args.delivery_id,
                args.version,
                args.outcome == "accepted",
                args.reason,
                evidence,
            )
            print(json.dumps({"resolved": True}))
        else:
            while not stopped:
                result = worker.run_once()
                print(json.dumps(result), flush=True)
                if args.once:
                    return (
                        1
                        if any(key in result for key in ("unknown", "failed", "busy"))
                        else 0
                    )
                for _ in range(150):
                    if stopped:
                        break
                    time.sleep(0.1)
        return 0
    except Exception:
        print(
            json.dumps(
                {
                    "error": "local_mail_operation_failed",
                    "message": "接続・設定・状態・根拠ファイルを確認してください",
                }
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
