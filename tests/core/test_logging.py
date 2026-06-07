"""ロギング設定のテスト。"""

import json
import logging

from app.core.logging import JsonFormatter


def test_json_formatter_includes_structured_log_fields() -> None:
    """JSONログに構造化フィールドが含まれることを確認する。"""
    record = logging.LogRecord(
        name="app.core.middleware",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Request completed",
        args=(),
        exc_info=None,
    )
    record.request_id = "json-log-test"
    record.method = "GET"
    record.path = "/users"
    record.status_code = 200
    record.duration_ms = 12.34
    record.client_ip = "127.0.0.1"
    record.user_agent = "test-client"
    record.is_slow = False

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "Request completed"
    assert payload["request_id"] == "json-log-test"
    assert payload["method"] == "GET"
    assert payload["path"] == "/users"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 12.34
    assert payload["client_ip"] == "127.0.0.1"
    assert payload["user_agent"] == "test-client"
    assert payload["is_slow"] is False
