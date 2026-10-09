"""アプリケーション設定を定義するモジュール。"""

import os
from dataclasses import dataclass, field
from email.headerregistry import Address
from email.utils import getaddresses
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def get_bool_env(name: str, default: bool = False) -> bool:
    """環境変数をbool値として取得する。

    Args:
        name: 環境変数名。
        default: 環境変数が未設定の場合の値。

    Returns:
        環境変数のbool値。
    """
    value = os.getenv(name)
    if value is None:
        return default

    return value.lower() in {"1", "true", "yes", "on"}


def get_required_env(name: str) -> str:
    """必須の環境変数を取得する。

    Args:
        name: 環境変数名。

    Returns:
        環境変数の値。

    Raises:
        RuntimeError: 環境変数が未設定の場合。
    """
    value = os.getenv(name)
    if value is None:
        raise RuntimeError(f"{name} is required")

    return value


def get_int_env(name: str, default: int) -> int:
    """環境変数をint値として取得する。

    Args:
        name: 環境変数名。
        default: 環境変数が未設定の場合の値。

    Returns:
        環境変数のint値。
    """
    value = os.getenv(name)
    if value is None:
        return default

    return int(value)


CookieSameSite = Literal["lax", "strict", "none"]
FileUploadMode = Literal["server", "presigned"]


def get_allowed_hosts() -> list[str]:
    """設定したHostと、Vercelが発行する実デプロイのHostを取得する。"""
    hosts = [
        host.strip()
        for host in os.getenv("ALLOWED_HOSTS", "").split(",")
        if host.strip()
    ]
    if os.getenv("VERCEL") == "1":
        for key in ("VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
            host = os.getenv(key)
            if host and host not in hosts:
                hosts.append(host)
    return hosts


def get_file_upload_mode() -> FileUploadMode:
    """アップロード方式を取得し、未対応の値は起動時に拒否する。"""
    value = os.getenv("FILE_UPLOAD_MODE", "server")
    if value == "server":
        return "server"
    if value == "presigned":
        return "presigned"
    raise RuntimeError("FILE_UPLOAD_MODE must be one of: server, presigned")


def get_file_upload_url_expires_seconds() -> int:
    """直接送信用URLの有効期間を起動時に検証する。"""
    value = get_int_env("FILE_UPLOAD_URL_EXPIRES_SECONDS", 600)
    if not 1 <= value <= 3600:
        raise RuntimeError("FILE_UPLOAD_URL_EXPIRES_SECONDS must be 1..3600")
    return value


def get_cookie_samesite_env(name: str, default: CookieSameSite) -> CookieSameSite:
    """Cookie SameSite属性の環境変数を取得する。

    Args:
        name: 環境変数名。
        default: 環境変数が未設定の場合の値。

    Returns:
        Cookie SameSite属性値。

    Raises:
        RuntimeError: 値が許可されていない場合。
    """
    value = os.getenv(name, default).lower()
    if value not in {"lax", "strict", "none"}:
        raise RuntimeError(f"{name} must be one of: lax, strict, none")

    return value  # type: ignore[return-value]


@dataclass
class Settings:
    """環境変数から読み込むアプリケーション設定。"""

    app_name: str = os.getenv("APP_NAME", "Syncnesto API")
    app_env: str = os.getenv("APP_ENV", "development")
    demo_mode: bool = get_bool_env("DEMO_MODE")
    demo_database_url: str = os.getenv("DEMO_DATABASE_URL", "")
    demo_secret_key: str = os.getenv("DEMO_SECRET_KEY", "")
    demo_aws_region: str = os.getenv("DEMO_AWS_REGION", "")
    demo_aws_access_key_id: str = os.getenv("DEMO_AWS_ACCESS_KEY_ID", "")
    demo_aws_secret_access_key: str = os.getenv("DEMO_AWS_SECRET_ACCESS_KEY", "")
    demo_aws_s3_bucket_name: str = os.getenv("DEMO_AWS_S3_BUCKET_NAME", "")
    demo_aws_s3_endpoint_url: str = os.getenv("DEMO_AWS_S3_ENDPOINT_URL", "")
    # Vercelは同じProject内のCronへCRON_SECRETを送る。接続先は各処理で固定する。
    demo_cron_secret: str = os.getenv("CRON_SECRET", "")
    demo_data_isolated: bool = get_bool_env("DEMO_DATA_ISOLATED")
    bff_shared_secret: str = os.getenv("BFF_SHARED_SECRET", "")
    mcp_enabled: bool = get_bool_env("MCP_ENABLED")
    mcp_issuer_url: str = os.getenv("MCP_ISSUER_URL", "http://127.0.0.1:8000")
    mcp_resource_url: str = os.getenv(
        "MCP_RESOURCE_URL",
        os.getenv("MCP_ISSUER_URL", "http://127.0.0.1:8000").rstrip("/") + "/mcp",
    )
    allowed_hosts: list[str] = field(default_factory=get_allowed_hosts)
    database_url: str = get_required_env("DATABASE_URL")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_format: str = os.getenv("LOG_FORMAT", "text")
    log_file: str | None = os.getenv("LOG_FILE")
    sql_echo: bool = get_bool_env("SQL_ECHO")
    slow_request_threshold_ms: int = get_int_env("SLOW_REQUEST_THRESHOLD_MS", 1000)
    secret_key: str = get_required_env("SECRET_KEY")
    algorithm: str = os.getenv("ALGORITHM", "HS256")
    access_token_expire_minutes: int = get_int_env(
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        30,
    )
    session_idle_timeout_minutes: int = get_int_env(
        "SESSION_IDLE_TIMEOUT_MINUTES",
        30,
    )
    session_refresh_threshold_minutes: int = get_int_env(
        "SESSION_REFRESH_THRESHOLD_MINUTES",
        10,
    )
    session_absolute_timeout_minutes: int = get_int_env(
        "SESSION_ABSOLUTE_TIMEOUT_MINUTES",
        480,
    )
    login_max_failed_attempts: int = get_int_env("LOGIN_MAX_FAILED_ATTEMPTS", 5)
    login_lock_minutes: int = get_int_env("LOGIN_LOCK_MINUTES", 15)
    audit_log_retention_days: int = get_int_env("AUDIT_LOG_RETENTION_DAYS", 1095)
    audit_log_cleanup_min_days: int = get_int_env("AUDIT_LOG_CLEANUP_MIN_DAYS", 30)
    deleted_data_retention_days: int = get_int_env("DELETED_DATA_RETENTION_DAYS", 30)
    deleted_data_cleanup_mode: str = os.getenv("DELETED_DATA_CLEANUP_MODE", "disabled")
    deleted_data_cleanup_tenant_ids: str = os.getenv(
        "DELETED_DATA_CLEANUP_TENANT_IDS", ""
    )
    deleted_data_cleanup_limit: int = get_int_env("DELETED_DATA_CLEANUP_LIMIT", 20)
    deleted_data_cleanup_budget_seconds: int = get_int_env(
        "DELETED_DATA_CLEANUP_BUDGET_SECONDS", 20
    )
    auth_cookie_name: str = os.getenv("AUTH_COOKIE_NAME", "access_token")
    auth_cookie_secure: bool = get_bool_env("AUTH_COOKIE_SECURE")
    auth_cookie_samesite: CookieSameSite = get_cookie_samesite_env(
        "AUTH_COOKIE_SAMESITE",
        "lax",
    )
    allow_bearer_token_response: bool = get_bool_env(
        "ALLOW_BEARER_TOKEN_RESPONSE",
        True,
    )
    allow_authorization_header: bool = get_bool_env(
        "ALLOW_AUTHORIZATION_HEADER",
        True,
    )
    csrf_cookie_name: str = os.getenv("CSRF_COOKIE_NAME", "csrf_token")
    csrf_header_name: str = os.getenv("CSRF_HEADER_NAME", "X-CSRF-Token")
    csrf_cookie_secure: bool = get_bool_env("CSRF_COOKIE_SECURE")
    csrf_cookie_samesite: CookieSameSite = get_cookie_samesite_env(
        "CSRF_COOKIE_SAMESITE",
        "lax",
    )
    initial_admin_email: str | None = os.getenv("INITIAL_ADMIN_EMAIL")
    initial_admin_password: str | None = os.getenv("INITIAL_ADMIN_PASSWORD")
    initial_admin_name: str = os.getenv("INITIAL_ADMIN_NAME", "Initial Admin")
    email_provider: str = os.getenv("EMAIL_PROVIDER", "disabled")
    email_from: str = os.getenv("EMAIL_FROM", "")
    frontend_public_url: str = os.getenv("FRONTEND_PUBLIC_URL", "http://localhost:3000")
    resend_api_key: str = field(default=os.getenv("RESEND_API_KEY", ""), repr=False)
    email_timeout_seconds: int = get_int_env("EMAIL_TIMEOUT_SECONDS", 10)
    account_action_expire_seconds: int = get_int_env(
        "ACCOUNT_ACTION_EXPIRE_SECONDS", 1800
    )
    smtp_host: str = os.getenv("SMTP_HOST", "127.0.0.1")
    smtp_port: int = get_int_env("SMTP_PORT", 1025)
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = field(default=os.getenv("SMTP_PASSWORD", ""), repr=False)
    smtp_starttls: bool = get_bool_env("SMTP_STARTTLS")
    aws_region: str = os.getenv(
        "AWS_REGION",
        os.getenv("AWS_DEFAULT_REGION", "ap-northeast-1"),
    )
    file_upload_mode: FileUploadMode = get_file_upload_mode()
    file_upload_url_expires_seconds: int = get_file_upload_url_expires_seconds()
    aws_access_key_id: str | None = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = os.getenv("AWS_SECRET_ACCESS_KEY")
    aws_s3_bucket_name: str = os.getenv(
        "AWS_S3_BUCKET_NAME",
        "syncnesto-local-app-bucket",
    )
    aws_s3_endpoint_url: str | None = os.getenv(
        "AWS_S3_ENDPOINT_URL",
        os.getenv("AWS_ENDPOINT_URL"),
    )
    aws_s3_presigned_url_expires_seconds: int = get_int_env(
        "AWS_S3_PRESIGNED_URL_EXPIRES_SECONDS",
        3600,
    )
    default_avatar_key: str = os.getenv("DEFAULT_AVATAR_KEY", "default-avatar.png")
    cors_origins: list[str] = field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
        ]
    )

    @property
    def is_public_environment(self) -> bool:
        """デモ機能の有無にかかわらず、本番の公開境界を適用する。"""
        return self.app_env == "production"

    def cleanup_tenant_ids(self) -> tuple[int, ...]:
        """定期回収で明示的に許可した組織だけを返す。"""
        raw = self.deleted_data_cleanup_tenant_ids.strip()
        if not raw:
            return ()
        parts = [value.strip() for value in raw.split(",")]
        if len(parts) > 20 or any(
            not value.isascii()
            or not value.isdigit()
            or len(value) > 10
            or not 1 <= int(value) <= 2**31 - 1
            for value in parts
        ):
            raise RuntimeError(
                "DELETED_DATA_CLEANUP_TENANT_IDS requires 1..20 positive IDs"
            )
        return tuple(dict.fromkeys(int(value) for value in parts))

    def validate_demo_database(self) -> None:
        """通常接続をデモへ流用せず、同じDBへの別資格情報も拒否する。"""
        from sqlalchemy.engine import make_url

        if not self.demo_data_isolated or not self.demo_database_url:
            raise RuntimeError(
                "Demo requires a dedicated database: "
                "DEMO_DATABASE_URL and DEMO_DATA_ISOLATED=true"
            )
        normal, demo = make_url(self.database_url), make_url(self.demo_database_url)
        if (
            demo.get_backend_name() != "postgresql"
            or not demo.host
            or not demo.database
        ):
            raise RuntimeError("DEMO_DATABASE_URL requires a PostgreSQL database host")

        def target(url):
            return (
                (url.host or "").lower().removesuffix(".").replace("-pooler.", "."),
                url.port or 5432,
                url.database,
            )

        if target(normal) == target(demo):
            raise RuntimeError(
                "DEMO_DATABASE_URL must not point to the normal database"
            )
        if self.is_public_environment and demo.query.get("sslmode") != "verify-full":
            raise RuntimeError("DEMO_DATABASE_URL requires sslmode=verify-full")

    def validate_demo_storage(self) -> None:
        """受付停止後の回収でも通常Storageへの誤接続を拒否する。"""
        if not all(
            (
                self.demo_aws_region,
                self.demo_aws_access_key_id,
                self.demo_aws_secret_access_key,
                self.demo_aws_s3_bucket_name,
                self.demo_aws_s3_endpoint_url,
            )
        ):
            raise RuntimeError("Demo requires dedicated DEMO_AWS_* storage settings")
        normal_endpoint = (
            (urlsplit(self.aws_s3_endpoint_url or "").hostname or "")
            .lower()
            .replace(".storage.supabase.co", ".supabase.co")
        )
        demo_endpoint = urlsplit(self.demo_aws_s3_endpoint_url)
        if (
            (demo_endpoint.hostname or "")
            .lower()
            .replace(".storage.supabase.co", ".supabase.co")
            == normal_endpoint
            or self.demo_aws_access_key_id == self.aws_access_key_id
            or self.demo_aws_secret_access_key == self.aws_secret_access_key
        ):
            raise RuntimeError(
                "Demo storage must use a separate Project and credentials"
            )
        if demo_endpoint.scheme != "https":
            raise RuntimeError("Demo storage requires HTTPS")

    def validate_cleanup(self) -> None:
        """誤設定による全組織への回収やデモとの混在を起動時に拒否する。"""
        if self.deleted_data_cleanup_mode not in {"disabled", "dry_run", "execute"}:
            raise RuntimeError(
                "DELETED_DATA_CLEANUP_MODE must be disabled, dry_run or execute"
            )
        if not 1 <= self.deleted_data_cleanup_limit <= 100:
            raise RuntimeError("DELETED_DATA_CLEANUP_LIMIT must be between 1 and 100")
        if not 1 <= self.deleted_data_cleanup_budget_seconds <= 40:
            raise RuntimeError(
                "DELETED_DATA_CLEANUP_BUDGET_SECONDS must be between 1 and 40"
            )
        tenant_ids = self.cleanup_tenant_ids()
        if self.deleted_data_cleanup_mode == "disabled":
            return
        if not tenant_ids:
            raise RuntimeError(
                "Scheduled cleanup requires DELETED_DATA_CLEANUP_TENANT_IDS"
            )
        if not 1 <= self.deleted_data_retention_days <= 3650:
            raise RuntimeError("Scheduled cleanup requires a finite positive retention")
        if (
            len(self.demo_cron_secret) < 32
            or self.demo_cron_secret == self.bff_shared_secret
        ):
            raise RuntimeError(
                "Scheduled cleanup requires a separate CRON_SECRET "
                "of at least 32 characters"
            )

    def validate_production(self) -> None:
        """公開環境の設定漏れを起動時に拒否する。"""
        if not 0 <= self.deleted_data_retention_days <= 3650:
            raise RuntimeError("DELETED_DATA_RETENTION_DAYS must be between 0 and 3650")
        if self.app_env not in {"development", "test", "production"}:
            raise RuntimeError(
                "APP_ENV must be one of: development, test, production; "
                "use DEMO_MODE=true for demo features"
            )
        self.validate_cleanup()
        if os.getenv("VERCEL") == "1" and not self.is_public_environment:
            raise RuntimeError("Vercel requires APP_ENV=production")
        if self.demo_mode:
            self.validate_demo_database()
            if len(self.demo_secret_key) < 32 or self.demo_secret_key in {
                self.secret_key,
                self.bff_shared_secret,
                self.demo_cron_secret,
            }:
                raise RuntimeError(
                    "Demo requires a separate DEMO_SECRET_KEY of at least 32 characters"
                )
            self.validate_demo_storage()
            if len(self.demo_cron_secret) < 32:
                raise RuntimeError(
                    "Demo requires CRON_SECRET with at least 32 characters"
                )
            origin = urlsplit(self.frontend_public_url)
            if (
                origin.scheme != "https"
                or not origin.netloc
                or origin.path not in {"", "/"}
            ):
                raise RuntimeError("Demo requires an HTTPS FRONTEND_PUBLIC_URL origin")
        if self.email_provider not in {"disabled", "smtp", "resend"}:
            raise RuntimeError("EMAIL_PROVIDER must be disabled, smtp or resend")
        if not 1 <= self.email_timeout_seconds <= 30:
            raise RuntimeError("EMAIL_TIMEOUT_SECONDS must be between 1 and 30")
        if not 300 <= self.account_action_expire_seconds <= 3600:
            raise RuntimeError(
                "ACCOUNT_ACTION_EXPIRE_SECONDS must be between 300 and 3600"
            )
        if self.email_provider != "disabled":
            origin = urlsplit(self.frontend_public_url)
            local_http = (
                not self.is_public_environment
                and origin.scheme == "http"
                and origin.hostname in {"localhost", "127.0.0.1"}
            )
            if (
                not origin.hostname
                or (origin.scheme != "https" and not local_http)
                or origin.username is not None
                or origin.password is not None
                or origin.path not in {"", "/"}
                or origin.query
                or origin.fragment
            ):
                raise RuntimeError("FRONTEND_PUBLIC_URL must be a trusted HTTPS origin")
            if (
                not self.email_from
                or "\n" in self.email_from
                or "\r" in self.email_from
            ):
                raise RuntimeError("EMAIL_FROM is required and must be a single line")
            if self.email_provider == "resend" and not self.resend_api_key:
                raise RuntimeError("RESEND_API_KEY is required for Resend")
            if self.email_provider == "smtp":
                self._validate_smtp()
        if not self.is_public_environment:
            return
        if len(self.bff_shared_secret) < 32:
            raise RuntimeError("Production requires BFF_SHARED_SECRET >= 32 characters")
        if len(self.secret_key) < 32 or self.secret_key.startswith("change-me"):
            raise RuntimeError("Production requires a strong SECRET_KEY")
        if not self.auth_cookie_secure or not self.csrf_cookie_secure:
            raise RuntimeError("Production requires Secure auth and CSRF cookies")
        if self.allow_bearer_token_response or self.allow_authorization_header:
            raise RuntimeError("Production requires Cookie-only authentication")
        if not self.allowed_hosts or any("*" in host for host in self.allowed_hosts):
            raise RuntimeError("Production requires explicit ALLOWED_HOSTS")
        connection = urlsplit(self.database_url)
        if parse_qs(connection.query).get("sslmode") != ["verify-full"]:
            raise RuntimeError("Production DATABASE_URL requires sslmode=verify-full")
        if self.sql_echo:
            raise RuntimeError("Production requires SQL_ECHO=false")

    def _validate_smtp(self) -> None:
        """SMTPの接続先と暗号化を検証し、本番ではGmailだけを許可する。"""
        if (
            not self.smtp_host
            or any(character.isspace() for character in self.smtp_host)
            or not 1 <= self.smtp_port <= 65535
            or bool(self.smtp_username) != bool(self.smtp_password)
        ):
            raise RuntimeError(
                "SMTP requires a valid host, port and paired credentials"
            )
        local_host = self.smtp_host.lower() in {
            "localhost",
            "127.0.0.1",
            "::1",
            "mailpit",
        }
        if (not local_host or self.smtp_username) and not self.smtp_starttls:
            raise RuntimeError("Remote or authenticated SMTP requires STARTTLS")
        gmail = self.smtp_host.lower() == "smtp.gmail.com"
        if self.is_public_environment and not gmail:
            raise RuntimeError("Production SMTP requires smtp.gmail.com")
        if not gmail:
            return
        if (
            self.smtp_port != 587
            or not self.smtp_starttls
            or not self.smtp_username
            or len(self.smtp_password) != 16
            or not self.smtp_password.isascii()
            or any(character.isspace() for character in self.smtp_password)
        ):
            raise RuntimeError(
                "Gmail SMTP requires port 587, STARTTLS and a 16-character app password"
            )
        try:
            username = Address(addr_spec=self.smtp_username)
            senders = getaddresses([self.email_from])
            if (
                not username.username
                or not username.domain
                or len(senders) != 1
                or senders[0][1].casefold() != self.smtp_username.casefold()
            ):
                raise ValueError
        except ValueError:
            raise RuntimeError(
                "Gmail EMAIL_FROM must match the authenticated SMTP_USERNAME address"
            ) from None


settings = Settings()
