"""アプリケーション全体のロギング設定を行うモジュール。"""

import json
import logging
import sys
from contextvars import ContextVar, Token
from logging import LogRecord
from typing import Any

from app.core.config import settings

LOG_EXTRA_FIELDS = (
    "method",
    "path",
    "status_code",
    "duration_ms",
    "client_ip",
    "user_agent",
    "is_slow",
    "exception_type",
)

request_id_context: ContextVar[str] = ContextVar(
    "request_id",
    default="-",
)
client_ip_context: ContextVar[str | None] = ContextVar(
    "client_ip",
    default=None,
)
user_agent_context: ContextVar[str | None] = ContextVar(
    "user_agent",
    default=None,
)


def set_request_id(request_id: str) -> Token[str]:
    """現在のコンテキストにrequest idを設定する。

    Args:
        request_id: リクエストを識別するID。

    Returns:
        request idをリセットするためのトークン。
    """
    return request_id_context.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    """現在のコンテキストのrequest idをリセットする。

    Args:
        token: request id設定時に返されたトークン。
    """
    request_id_context.reset(token)


def set_request_metadata(
    *,
    client_ip: str | None,
    user_agent: str | None,
) -> tuple[Token[str | None], Token[str | None]]:
    """現在のコンテキストにリクエストメタデータを設定する。

    Args:
        client_ip: 接続元IPアドレス。
        user_agent: User-Agent文字列。

    Returns:
        client_ipとuser_agentをリセットするためのトークン。
    """
    return (
        client_ip_context.set(client_ip),
        user_agent_context.set(user_agent),
    )


def reset_request_metadata(
    client_ip_token: Token[str | None],
    user_agent_token: Token[str | None],
) -> None:
    """現在のコンテキストのリクエストメタデータをリセットする。

    Args:
        client_ip_token: client_ip設定時に返されたトークン。
        user_agent_token: user_agent設定時に返されたトークン。
    """
    client_ip_context.reset(client_ip_token)
    user_agent_context.reset(user_agent_token)


class RequestIdFilter(logging.Filter):
    """ログレコードにrequest idを追加するFilter。"""

    def filter(self, record: LogRecord) -> bool:
        """ログレコードにrequest idを追加する。

        Args:
            record: ログレコード。

        Returns:
            常にTrue。
        """
        record.request_id = request_id_context.get()
        return True


class JsonFormatter(logging.Formatter):
    """ログレコードをJSON形式に変換するFormatter。"""

    def format(self, record: LogRecord) -> str:
        """ログレコードをJSON文字列に変換する。

        Args:
            record: ログレコード。

        Returns:
            JSON形式のログ文字列。
        """
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        for field in LOG_EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)

        return json.dumps(payload, ensure_ascii=False)


def get_log_level(level_name: str) -> int:
    """ログレベル名に対応するloggingレベルを取得する。

    Args:
        level_name: ログレベル名。

    Returns:
        loggingで使用するログレベル。
    """
    return getattr(logging, level_name.upper(), logging.INFO)


def create_formatter() -> logging.Formatter:
    """環境変数の設定に応じたFormatterを作成する。

    Returns:
        ログ出力に使用するFormatter。
    """
    if settings.log_format.lower() == "json":
        return JsonFormatter()

    return logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] "
        "[request_id=%(request_id)s] %(message)s"
    )


def configure_logging() -> None:
    """アプリケーション全体のロギング設定を初期化する。"""
    log_level = get_log_level(settings.log_level)
    formatter = create_formatter()
    request_id_filter = RequestIdFilter()

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if settings.log_file:
        handlers.append(logging.FileHandler(settings.log_file))

    for handler in handlers:
        handler.setLevel(log_level)
        handler.setFormatter(formatter)
        handler.addFilter(request_id_filter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)

    for handler in handlers:
        root_logger.addHandler(handler)

    logging.getLogger("uvicorn.access").setLevel(log_level)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.sql_echo else logging.WARNING
    )
