"""CI専用の管理URIで本番migrationを実行し、資格情報をログに出さない。"""

import os
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import certifi
from dotenv import dotenv_values


def migration_targets(
    env: dict[str, str], deployed: dict[str, str | None]
) -> list[tuple[str, str]]:
    """通常接続を保持し、デモ専用接続がある場合は両方を先に検査する。"""
    targets = [("normal", env.get("MIGRATION_DATABASE_URL", ""))]
    truthy = {"1", "true", "yes", "on"}
    if any(
        (deployed.get(key) or "").lower() in truthy
        for key in ("DEMO_MODE", "DEMO_DATA_ISOLATED")
    ):
        targets.append(("demo", env.get("DEMO_MIGRATION_DATABASE_URL", "")))
    hosts = set()
    for _realm, uri in targets:
        parsed = urlsplit(uri)
        if (
            parsed.scheme != "postgresql"
            or parsed.username != "syncnesto_owner"
            or not parsed.hostname
            or not parsed.hostname.endswith(".neon.tech")
            or "-pooler" in parsed.hostname
            or parsed.path != "/syncnesto"
            or parse_qs(parsed.query).get("sslmode") != ["verify-full"]
            or parsed.hostname in hosts
        ):
            raise ValueError("Separate verified Neon owner URIs are required")
        hosts.add(parsed.hostname)
    return targets


def main() -> int:
    """TLSを検証するNeon direct接続だけを許可する。"""
    root = Path(__file__).resolve().parents[1]
    deployed = dotenv_values(root / ".vercel/.env.production.local")
    try:
        targets = migration_targets(dict(os.environ), deployed)
    except ValueError:
        print("Set separate verified production Neon owner URIs.", file=sys.stderr)
        return 2
    for realm, uri in targets:
        result = migrate(uri)
        if result:
            print(f"{realm} migration failed; deployment stopped.", file=sys.stderr)
            return result
        print(f"{realm} migration completed.")
    return 0


def migrate(uri: str) -> int:
    """検査済み接続だけを子プロセスへ渡し、生ログは公開しない。"""
    env = os.environ.copy()
    env.pop("MIGRATION_DATABASE_URL", None)
    env.pop("DEMO_MIGRATION_DATABASE_URL", None)
    env.update(
        {
            "DATABASE_URL": uri,
            "PGSSLROOTCERT": certifi.where(),
            "APP_ENV": "production",
            # migrationはデモAPIを起動せず、デモ用runtime設定を引き継がない。
            "DEMO_MODE": "false",
            "DEMO_DATABASE_URL": "",
            "DEMO_DATA_ISOLATED": "false",
            "EMAIL_PROVIDER": "disabled",
            "DELETED_DATA_CLEANUP_MODE": "disabled",
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
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
