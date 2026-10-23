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
    smtp_exchange,
)

pytestmark = pytest.mark.no_db


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
    monkeypatch.setattr(settings, "fixed_approval_email_enabled", True)
    monkeypatch.setattr(settings, "fixed_approval_notifications_enabled", True)
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
