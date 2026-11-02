"""既存送信契約から独立したSMTP段階の診断。"""

import multiprocessing
import smtplib
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.services.email import OutgoingEmail
from app.services.notification_email_transport import (
    LocalApprovalMailTransport,
    configured_smtp,
    smtp_exchange,
)

pytestmark = pytest.mark.no_db


def test_production_smtp_uses_existing_tls_and_auth_configuration(monkeypatch):
    """本番は既存接続先を使い、LOCAL捕捉先へ配送しない。"""
    for name, value in dict(
        app_env="production",
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_starttls=True,
        smtp_username="dummy-user",
        smtp_password="dummy-secret",
    ).items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(
        "app.services.email.EmailService.ensure_available", lambda _: None
    )
    smtp = MagicMock()
    factory = MagicMock(return_value=smtp)
    monkeypatch.setattr(
        "app.services.notification_email_transport.smtplib.SMTP", factory
    )
    assert configured_smtp("127.0.0.1", 1025, timeout=3) is smtp
    factory.assert_called_once_with("smtp.example.test", 587, timeout=3)
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("dummy-user", "dummy-secret")


@pytest.mark.parametrize(
    "stage,error,outcome",
    [
        ("connect", ConnectionRefusedError(), "temporary"),
        ("data", smtplib.SMTPServerDisconnected(), "unknown"),
        ("data", smtplib.SMTPDataError(451, b"private response"), "temporary"),
        ("data", smtplib.SMTPDataError(550, b"private response"), "failed"),
        ("quit", smtplib.SMTPServerDisconnected(), "accepted"),
    ],
)
def test_smtp_reply_boundaries(stage, error, outcome):
    """DATA受付不明と既に確認した肯定応答を混同しない。"""
    client = MagicMock()
    for name in ("ehlo", "mail", "rcpt", "data"):
        getattr(client, name).return_value = (250, b"ok")
    factory = MagicMock(return_value=client)
    if stage == "connect":
        factory.side_effect = error
    else:
        getattr(client, stage).side_effect = error
    message = OutgoingEmail(
        "ux-mail@example.com", "通知", "概要", "<p>概要</p>", "test/1"
    )
    result = smtp_exchange(message, "Syncnesto <local@example.com>", factory)
    assert result.outcome == outcome
    assert "private response" not in repr(result)
    if stage == "quit":
        assert result.message_id


def sleep_child(*args):
    """spawnできるdeadline検証用の停止しない子。"""
    time.sleep(30)


def test_transport_total_deadline_reaps_child(monkeypatch):
    """socket timeoutへ依存せず、認可期限で子processを回収する。"""
    monkeypatch.setenv("FIXED_APPROVAL_EMAIL_ENABLED", "true")
    monkeypatch.setenv("FIXED_APPROVAL_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setattr(
        settings, "local_email_capture_recipients", "ux-mail@example.com"
    )
    monkeypatch.setattr(
        "app.services.notification_email_transport._capture_child", sleep_child
    )
    message = OutgoingEmail("ux-mail@example.com", "通知", "概要", "概要", "test/1")
    started = time.monotonic()
    result = LocalApprovalMailTransport().send(
        message, MagicMock(), datetime.now(UTC) + timedelta(seconds=0.4)
    )
    assert result.outcome == "unknown"
    assert time.monotonic() - started < 2
    assert not multiprocessing.active_children()
