"""配信アダプターの成功・失敗・秘密情報の非公開を検証する。"""

import json
import smtplib
import ssl
from dataclasses import replace
from email.message import EmailMessage
from unittest.mock import MagicMock

import httpx
import pytest

from app.core.config import settings
from app.core.exceptions import EmailUnavailableError
from app.services.email import EmailService, OutgoingEmail

pytestmark = pytest.mark.no_db

SECRET_API_KEY = "re_email-adapter-test-secret"
SECRET_BODY = "https://localhost/account/action#token=secret-email-test-token"
GMAIL_APP_PASSWORD = "gmail-test-key16"


@pytest.fixture
def outgoing() -> OutgoingEmail:
    """本人確認リンクを含むテストメールを作る。"""
    return OutgoingEmail(
        to="recipient@example.com",
        subject="メールアドレスの変更確認",
        text=SECRET_BODY,
        html=f'<a href="{SECRET_BODY}">確認する</a>',
        idempotency_key="account-action/test-id/confirm-new",
    )


@pytest.fixture(autouse=True)
def configure_email(monkeypatch: pytest.MonkeyPatch) -> None:
    """外部へ配信しないテスト用設定を用意する。"""
    monkeypatch.setattr(settings, "email_provider", "resend")
    monkeypatch.setattr(settings, "email_from", "Syncnesto <no-reply@example.com>")
    monkeypatch.setattr(settings, "resend_api_key", SECRET_API_KEY)
    monkeypatch.setattr(settings, "email_timeout_seconds", 10)
    monkeypatch.setattr(settings, "frontend_public_url", "http://localhost:3000")
    monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(settings, "smtp_port", 1025)
    monkeypatch.setattr(settings, "smtp_username", "")
    monkeypatch.setattr(settings, "smtp_password", "")
    monkeypatch.setattr(settings, "smtp_starttls", False)


@pytest.fixture
def configure_gmail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gmail本番設定を用意する。資格情報は送信しないテスト用ダミー。"""
    values = {
        "app_env": "production",
        "email_provider": "smtp",
        "email_from": "Syncnesto <sender@gmail.com>",
        "frontend_public_url": "https://syncnesto.example.com",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_username": "sender@gmail.com",
        "smtp_password": GMAIL_APP_PASSWORD,
        "smtp_starttls": True,
        "bff_shared_secret": "bff-test-" * 8,
        "secret_key": "signing-test-" * 8,
        "auth_cookie_secure": True,
        "csrf_cookie_secure": True,
        "allow_bearer_token_response": False,
        "allow_authorization_header": False,
        "allowed_hosts": ["api.syncnesto.example.com"],
        "database_url": "postgresql://test:test@db.example.com/test?sslmode=verify-full",
        "sql_echo": False,
    }
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value)


def test_resend_keeps_payload_and_idempotency_key_on_retry(
    outgoing: OutgoingEmail,
) -> None:
    """宛先・本文と冪等性キーを送信し、受付IDを返す。"""
    requests: list[httpx.Request] = []

    def accept(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "resend-message-id"})

    service = EmailService(http_transport=httpx.MockTransport(accept))
    assert service.send(outgoing) == "resend-message-id"
    assert service.send(outgoing) == "resend-message-id"
    assert len(requests) == 2
    assert requests[0].url == "https://api.resend.com/emails"
    assert requests[0].method == "POST"
    assert requests[0].headers["Authorization"] == f"Bearer {SECRET_API_KEY}"
    assert requests[0].headers["Idempotency-Key"] == outgoing.idempotency_key
    assert requests[1].headers["Idempotency-Key"] == outgoing.idempotency_key
    assert requests[0].content == requests[1].content
    assert set(requests[0].extensions["timeout"].values()) == {10}
    assert json.loads(requests[0].content) == {
        "from": settings.email_from,
        "to": [outgoing.to],
        "subject": outgoing.subject,
        "text": outgoing.text,
        "html": outgoing.html,
    }


@pytest.mark.parametrize("status", [302, 400, 401, 403, 409, 422, 429, 500, 503])
def test_resend_errors_are_generic_and_do_not_log_secrets(
    status: int,
    outgoing: OutgoingEmail,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """エラー本文に秘密が反射されても通常ログや公開例外に残さない。"""
    requests: list[httpx.Request] = []

    def reject(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status,
            headers={"Location": "https://another.example.com/emails"},
            json={"message": f"rejected {SECRET_API_KEY} {SECRET_BODY}"},
        )

    service = EmailService(http_transport=httpx.MockTransport(reject))
    with pytest.raises(EmailUnavailableError) as caught:
        service.send(outgoing)

    assert len(requests) == 1
    for secret in (SECRET_API_KEY, SECRET_BODY):
        assert secret not in str(caught.value)
        assert secret not in caplog.text
    assert caught.value.__cause__ is None


def test_resend_timeout_is_generic(
    outgoing: OutgoingEmail, caplog: pytest.LogCaptureFixture
) -> None:
    """タイムアウトを配信失敗へ変換し、元例外にある秘密を隠す。"""

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(
            f"timeout {SECRET_API_KEY} {SECRET_BODY}", request=request
        )

    service = EmailService(http_transport=httpx.MockTransport(timeout))
    with pytest.raises(EmailUnavailableError) as caught:
        service.send(outgoing)
    assert caught.value.__suppress_context__
    assert SECRET_API_KEY not in str(caught.value) + caplog.text
    assert SECRET_BODY not in str(caught.value) + caplog.text


@pytest.mark.parametrize("payload", [{}, {"id": None}, {"id": 123}, [], {"id": ""}])
def test_resend_rejects_success_without_message_id(
    payload: object, outgoing: OutgoingEmail
) -> None:
    """受付IDがない応答を送信成功として扱わない。"""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(EmailUnavailableError):
        EmailService(http_transport=transport).send(outgoing)


def test_resend_rejects_invalid_json(outgoing: OutgoingEmail) -> None:
    """不正な応答本文を公開せず、配信失敗として扱う。"""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=SECRET_BODY)
    )
    with pytest.raises(EmailUnavailableError):
        EmailService(http_transport=transport).send(outgoing)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("email_provider", "disabled"),
        ("email_provider", "unknown"),
        ("email_from", ""),
        ("email_from", "not-an-address"),
        ("email_from", "Sender <sender@example.com>\r\nBcc: other@example.com"),
        ("resend_api_key", ""),
        ("email_timeout_seconds", 0),
        ("frontend_public_url", "http://attacker.example.com"),
        ("frontend_public_url", "https://user:password@example.com"),
    ],
)
def test_missing_configuration_cannot_send(
    key: str,
    value: object,
    outgoing: OutgoingEmail,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """無効な配信設定を送信前に拒否する。"""
    monkeypatch.setattr(settings, key, value)
    transport = MagicMock(spec=httpx.BaseTransport)
    service = EmailService(http_transport=transport)
    with pytest.raises(EmailUnavailableError):
        service.ensure_available()
    with pytest.raises(EmailUnavailableError):
        service.send(outgoing)
    transport.handle_request.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("to", "recipient@example.com,other@example.com"),
        ("to", "recipient@example.com\nBcc: other@example.com"),
        ("subject", "subject\r\nBcc: other@example.com"),
        ("idempotency_key", ""),
        ("idempotency_key", "x" * 257),
        ("idempotency_key", "action\nAuthorization: injected"),
    ],
)
def test_message_headers_are_validated_before_sending(
    field: str, value: str, outgoing: OutgoingEmail
) -> None:
    """ヘッダー挿入や複数宛先を配信前に拒否する。"""
    transport = MagicMock(spec=httpx.BaseTransport)
    with pytest.raises(EmailUnavailableError):
        EmailService(http_transport=transport).send(replace(outgoing, **{field: value}))
    transport.handle_request.assert_not_called()


def test_outgoing_repr_excludes_confirmation_link(outgoing: OutgoingEmail) -> None:
    """本人確認URLが通常のdataclass表現に現れない。"""
    assert SECRET_BODY not in repr(outgoing)


def test_smtp_sends_multipart_to_one_recipient(
    outgoing: OutgoingEmail,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SMTPのenvelope宛先とplain/html、Message-IDを確認する。"""
    monkeypatch.setattr(settings, "email_provider", "smtp")
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.return_value = {}
    factory = MagicMock(return_value=smtp)
    monkeypatch.setattr("app.services.email.smtplib.SMTP", factory)

    service = EmailService()
    message_id = service.send(outgoing)
    factory.assert_called_once_with("127.0.0.1", 1025, timeout=10)
    sent: EmailMessage = smtp.send_message.call_args.args[0]
    assert smtp.send_message.call_args.kwargs == {
        "from_addr": "no-reply@example.com",
        "to_addrs": [outgoing.to],
    }
    assert str(sent["Message-ID"]) == message_id
    assert str(sent["Subject"]) == outgoing.subject
    assert sent.get_content_type() == "multipart/alternative"
    parts = list(sent.iter_parts())
    assert [part.get_content_type() for part in parts] == ["text/plain", "text/html"]
    assert SECRET_BODY in parts[0].get_content()
    assert SECRET_BODY in parts[1].get_content()
    assert service.send(outgoing) == message_id
    smtp.starttls.assert_not_called()
    smtp.login.assert_not_called()


def test_smtp_uses_starttls_before_authentication(
    outgoing: OutgoingEmail,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """設定されたSMTP認証をTLSの開始後に行う。"""
    monkeypatch.setattr(settings, "email_provider", "smtp")
    monkeypatch.setattr(settings, "smtp_starttls", True)
    monkeypatch.setattr(settings, "smtp_username", "smtp-test-user")
    monkeypatch.setattr(settings, "smtp_password", "smtp-test-password")
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.return_value = {}
    monkeypatch.setattr("app.services.email.smtplib.SMTP", MagicMock(return_value=smtp))

    EmailService().send(outgoing)
    methods = [call[0] for call in smtp.method_calls]
    assert (
        methods.index("starttls")
        < methods.index("login")
        < methods.index("send_message")
    )
    smtp.login.assert_called_once_with("smtp-test-user", "smtp-test-password")


def test_gmail_sends_only_after_verified_tls_and_authentication(
    configure_gmail: None,
    outgoing: OutgoingEmail,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本番Gmailで証明書検証TLS、再EHLO、認証の後に1人へ配送する。"""
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.return_value = {}
    factory = MagicMock(return_value=smtp)
    monkeypatch.setattr("app.services.email.smtplib.SMTP", factory)

    message_id = EmailService().send(outgoing)

    factory.assert_called_once_with("smtp.gmail.com", 587, timeout=10)
    methods = [call[0] for call in smtp.method_calls]
    assert (
        methods.index("starttls")
        < methods.index("ehlo")
        < methods.index("login")
        < methods.index("send_message")
    )
    context = smtp.starttls.call_args.kwargs["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    smtp.login.assert_called_once_with("sender@gmail.com", GMAIL_APP_PASSWORD)
    assert smtp.send_message.call_args.kwargs == {
        "from_addr": "sender@gmail.com",
        "to_addrs": [outgoing.to],
    }
    assert message_id.endswith("@gmail.com>")
    assert GMAIL_APP_PASSWORD not in repr(settings)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("smtp_host", "127.0.0.1"),
        ("smtp_host", "smtp.example.com"),
        ("smtp_host", "smtp.gmail.com\n"),
        ("smtp_port", 465),
        ("smtp_port", 25),
        ("smtp_starttls", False),
        ("smtp_username", ""),
        ("smtp_username", "sender"),
        ("smtp_password", ""),
        ("smtp_password", "ordinary-account-password"),
        ("smtp_password", "abcd efgh ijkl mnop"),
        ("email_from", "Syncnesto <another@gmail.com>"),
        ("email_from", "sender@gmail.com,other@gmail.com"),
        ("frontend_public_url", "http://localhost:3000"),
    ],
)
def test_invalid_gmail_configuration_fails_before_connection(
    configure_gmail: None,
    key: str,
    value: object,
    outgoing: OutgoingEmail,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本番Gmailの設定ミスでネットワーク接続や認証を開始しない。"""
    monkeypatch.setattr(settings, key, value)
    factory = MagicMock()
    monkeypatch.setattr("app.services.email.smtplib.SMTP", factory)

    with pytest.raises(EmailUnavailableError):
        EmailService().send(outgoing)

    factory.assert_not_called()


@pytest.mark.parametrize("host", ["smtp.gmail.com", "smtp.example.com", "localhost"])
def test_smtp_never_authenticates_without_tls(
    host: str,
    outgoing: OutgoingEmail,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """開発環境でも平文SMTPへの資格情報送信を拒否する。"""
    monkeypatch.setattr(settings, "email_provider", "smtp")
    monkeypatch.setattr(settings, "smtp_host", host)
    monkeypatch.setattr(settings, "smtp_username", "sender@gmail.com")
    monkeypatch.setattr(settings, "smtp_password", GMAIL_APP_PASSWORD)
    factory = MagicMock()
    monkeypatch.setattr("app.services.email.smtplib.SMTP", factory)

    with pytest.raises(EmailUnavailableError):
        EmailService().send(outgoing)

    factory.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        smtplib.SMTPNotSupportedError(SECRET_BODY),
        ssl.SSLCertVerificationError(SECRET_BODY),
    ],
)
def test_gmail_tls_failure_never_falls_back_to_plaintext(
    configure_gmail: None,
    failure: Exception,
    outgoing: OutgoingEmail,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """STARTTLSや証明書検証が失敗したら認証・配送せず詳細も公開しない。"""
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.starttls.side_effect = failure
    monkeypatch.setattr("app.services.email.smtplib.SMTP", MagicMock(return_value=smtp))

    with pytest.raises(EmailUnavailableError) as caught:
        EmailService().send(outgoing)

    smtp.login.assert_not_called()
    smtp.send_message.assert_not_called()
    assert SECRET_BODY not in str(caught.value) + caplog.text
    assert GMAIL_APP_PASSWORD not in str(caught.value) + caplog.text
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("reject_recipient", [True, False])
def test_smtp_failure_does_not_expose_provider_details(
    reject_recipient: bool,
    outgoing: OutgoingEmail,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """宛先拒否とSMTP例外を、秘密を含まない配信失敗へ変換する。"""
    monkeypatch.setattr(settings, "email_provider", "smtp")
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    if reject_recipient:
        smtp.send_message.return_value = {outgoing.to: (550, SECRET_BODY.encode())}
    else:
        smtp.send_message.side_effect = smtplib.SMTPException(SECRET_BODY)
    monkeypatch.setattr("app.services.email.smtplib.SMTP", MagicMock(return_value=smtp))

    with pytest.raises(EmailUnavailableError) as caught:
        EmailService().send(outgoing)
    assert SECRET_BODY not in str(caught.value) + caplog.text


def test_smtp_partial_login_configuration_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SMTP資格情報の片方だけが設定されている場合は送信を開始しない。"""
    monkeypatch.setattr(settings, "email_provider", "smtp")
    monkeypatch.setattr(settings, "smtp_username", "configured-user")
    with pytest.raises(EmailUnavailableError):
        EmailService().ensure_available()


def test_production_cannot_use_local_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    """本番で開発用SMTPを有効にできない。"""
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "email_provider", "smtp")
    with pytest.raises(EmailUnavailableError):
        EmailService().ensure_available()
