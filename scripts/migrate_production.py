"""CI専用の管理URIで本番migrationを実行し、資格情報をログに出さない。"""

import os
import secrets
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import certifi


def main() -> int:
    """TLSを検証するNeon direct接続だけを許可する。"""
    uri = os.getenv("MIGRATION_DATABASE_URL", "")
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "postgresql"
        or parsed.username != "syncnesto_owner"
        or not parsed.hostname
        or not parsed.hostname.endswith(".neon.tech")
        or "-pooler" in parsed.hostname
        or parsed.path != "/syncnesto"
        or parse_qs(parsed.query).get("sslmode") != ["verify-full"]
    ):
        print("Set the verified production Neon owner URI.", file=sys.stderr)
        return 2
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": uri,
            "PGSSLROOTCERT": certifi.where(),
            "APP_ENV": "production",
            # migrationはデモAPIを起動せず、デモ用runtime設定を引き継がない。
            "DEMO_MODE": "false",
            # migrationはJWTを発行せず、アプリの実際の署名キーも必要としない。
            "SECRET_KEY": secrets.token_urlsafe(48),
            "BFF_SHARED_SECRET": secrets.token_urlsafe(48),
            "ALLOWED_HOSTS": "syncnesto-portfolio-api.vercel.app",
            "AUTH_COOKIE_SECURE": "true",
            "CSRF_COOKIE_SECURE": "true",
            "ALLOW_BEARER_TOKEN_RESPONSE": "false",
            "ALLOW_AUTHORIZATION_HEADER": "false",
            "SQL_ECHO": "false",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        print("Production migration failed; deployment stopped.", file=sys.stderr)
        return result.returncode
    print("Production migration completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
