"""アプリケーション設定を定義するモジュール。"""

from dataclasses import dataclass, field
import os
from pathlib import Path

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


@dataclass
class Settings:
    """環境変数から読み込むアプリケーション設定。"""

    app_name: str = os.getenv("APP_NAME", "Syncnesto API")
    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str = get_required_env("DATABASE_URL")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_format: str = os.getenv("LOG_FORMAT", "text")
    log_file: str | None = os.getenv("LOG_FILE")
    sql_echo: bool = get_bool_env("SQL_ECHO")
    secret_key: str = get_required_env("SECRET_KEY")
    algorithm: str = os.getenv("ALGORITHM", "HS256")
    access_token_expire_minutes: int = get_int_env(
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        30,
    )
    cors_origins: list[str] = field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
        ]
    )


settings = Settings()
