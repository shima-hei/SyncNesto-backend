"""アプリケーション設定を定義するモジュール。"""

import os
from dataclasses import dataclass, field
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
    bff_shared_secret: str = os.getenv("BFF_SHARED_SECRET", "")
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

    def validate_production(self) -> None:
        """公開環境の設定漏れを起動時に拒否する。"""
        if self.app_env != "production":
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


settings = Settings()
