"""ダミー配送だけを隔離し、既存SMTPへ漏れないことを確認する。"""

from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.core.exceptions import EmailUnavailableError, LocalEmailCaptureError
from app.services.email import EmailService, OutgoingEmail

pytestmark = pytest.mark.no_db


@pytest.fixture
def capture(monkeypatch):
    """実際の外部接続は行わない。"""
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(
        settings, "local_email_capture_recipients", "ux-dummy@example.com"
    )
    monkeypatch.setattr(settings, "local_email_capture_host", "127.0.0.1")
    monkeypatch.setattr(settings, "local_email_capture_port", 1025)
    client = MagicMock()
    client.__enter__.return_value = client
    client.send_message.return_value = {}
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr("app.services.email.smtplib.SMTP", constructor)
    return client, constructor


def message(recipient="ux-dummy@example.com"):
    """招待と通常MFA通知に共通のメッセージ形式。"""
    return OutgoingEmail(
        to=recipient,
        subject="test",
        text="secret-body",
        html="<p>secret-body</p>",
        idempotency_key="test-capture",
    )


def test_dummy_only_uses_loopback_without_credentials(capture, monkeypatch):
    """通常SMTPの秘密を読まず、捕捉先だけへ接続する。"""
    client, constructor = capture
    monkeypatch.setattr(
        EmailService,
        "ensure_available",
        lambda self: pytest.fail("normal provider used"),
    )
    assert EmailService().send(message()).endswith("@example.com>")
    constructor.assert_called_once_with("127.0.0.1", 1025, timeout=10)
    client.login.assert_not_called()
    client.starttls.assert_not_called()


def test_other_recipient_keeps_original_adapter(capture, monkeypatch):
    """非対象の既存配送を変更しない。実際には送信しない。"""
    _, constructor = capture
    normal = MagicMock(return_value="normal-id")
    monkeypatch.setattr(settings, "email_provider", "smtp")
    monkeypatch.setattr(EmailService, "ensure_available", lambda self: None)
    monkeypatch.setattr(EmailService, "_send_smtp", normal)
    assert EmailService().send(message("original@example.net")) == "normal-id"
    normal.assert_called_once()
    constructor.assert_not_called()


def test_capture_failure_never_falls_back(capture, monkeypatch):
    """結果不明でもremote SMTPへ再配送しない。"""
    _, constructor = capture
    constructor.side_effect = OSError("unavailable")
    monkeypatch.setattr(
        EmailService, "ensure_available", lambda self: pytest.fail("fallback")
    )
    with pytest.raises(EmailUnavailableError):
        EmailService().send(message())


@pytest.mark.parametrize("during_send", [False, True])
def test_capture_distinguishes_pre_delivery_failure_and_unknown(capture, during_send):
    """未接続と送信中断の確度を混同せず、自動再送しない。"""
    client, constructor = capture
    if during_send:
        client.send_message.side_effect = TimeoutError("test only")
    else:
        constructor.side_effect = ConnectionRefusedError("test only")
    with pytest.raises(LocalEmailCaptureError) as error:
        EmailService().send(message())
    assert error.value.uncertain is during_send


@pytest.mark.parametrize(
    "key,value",
    [
        ("app_env", "production"),
        ("local_email_capture_host", "smtp.example.net"),
        ("local_email_capture_port", 587),
        ("local_email_capture_recipients", "real@example.net"),
    ],
)
def test_invalid_capture_settings_fail_closed(capture, monkeypatch, key, value):
    """公開環境・遠隔配送先・非ダミーallowlistを拒否する。"""
    monkeypatch.setattr(settings, key, value)
    with pytest.raises(EmailUnavailableError):
        EmailService().send(message())
    capture[1].assert_not_called()
