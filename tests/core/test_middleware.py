"""Middlewareのテスト。"""

import logging

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings


def test_request_logging_middleware_logs_completed_request(
    client: TestClient,
    caplog,
) -> None:
    """リクエスト完了時に通常ログを出力することを確認する。"""
    caplog.set_level(logging.INFO, logger="app.core.middleware")

    response = client.get(
        "/?token=secret",
        headers={"X-Request-ID": "request-log-test"},
    )

    log_record = next(
        record
        for record in caplog.records
        if record.name == "app.core.middleware"
        and record.getMessage() == "Request completed"
    )
    assert response.status_code == 200
    assert log_record.levelno == logging.INFO
    assert log_record.method == "GET"
    assert log_record.path == "/"
    assert log_record.status_code == 200
    assert isinstance(log_record.duration_ms, float)
    assert log_record.request_id == "request-log-test"
    assert log_record.is_slow is False
    assert "token=secret" not in log_record.path


def test_request_logging_middleware_warns_slow_request(
    client: TestClient,
    caplog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """閾値以上のリクエストをWARNINGログとして出力することを確認する。"""
    monkeypatch.setattr(settings, "slow_request_threshold_ms", 0)
    caplog.set_level(logging.WARNING, logger="app.core.middleware")

    response = client.get("/")

    log_record = next(
        record
        for record in caplog.records
        if record.name == "app.core.middleware"
        and record.getMessage() == "Slow request completed"
    )
    assert response.status_code == 200
    assert log_record.levelno == logging.WARNING
    assert log_record.is_slow is True
    assert log_record.status_code == 200
