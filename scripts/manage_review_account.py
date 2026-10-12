"""MCP公開審査専用fixtureの発行と失効。資格情報は指定した私有ファイルだけへ保存する。"""

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe

from app.core.config import settings
from app.db.session import session_local
from app.services.review_fixture import ReviewFixtureService


def create(output: Path, days: int) -> None:
    """新規Identityだけを発行する。既存利用者のMFAは変更しない。"""
    if not 1 <= days <= 90:
        raise ValueError("有効期間は1〜90日で指定してください")
    if output.exists():
        raise ValueError("資格情報ファイルは上書きしません")
    password = token_urlsafe(32)
    with session_local() as db:
        fixture = ReviewFixtureService().create(
            db, password, datetime.now(UTC) + timedelta(days=days)
        )
        fixture.update(
            password=password,
            login_url=settings.frontend_public_url.rstrip("/") + "/login",
        )
        with os.fdopen(
            os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
        ) as file:
            json.dump(fixture, file, ensure_ascii=False, indent=2)
        db.commit()
    print(
        "審査専用Identityとサンプルを作成しました。資格情報は指定ファイルだけに保存しました。"
    )


def main() -> None:
    """CLIの明示操作だけで発行または失効を行う。"""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("create")
    issue.add_argument("--output", required=True, type=Path)
    issue.add_argument("--days", type=int, default=30)
    revoke = commands.add_parser("revoke")
    revoke.add_argument("--user-id", required=True, type=int)
    args = parser.parse_args()
    if args.command == "create":
        create(args.output, args.days)
    else:
        with session_local() as db:
            ReviewFixtureService().revoke(db, args.user_id)
            db.commit()
        print("審査用許可と既存のWeb・MCP接続を失効しました。")


if __name__ == "__main__":
    main()
