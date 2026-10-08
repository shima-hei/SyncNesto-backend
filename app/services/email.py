"""外部配信サービスと暗号化SMTP、ローカルMailpitへの送信を提供する。"""

import hashlib
import smtplib
import ssl
from dataclasses import dataclass, field
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import getaddresses

import httpx

from app.core.config import settings
from app.core.exceptions import EmailUnavailableError

RESEND_EMAILS_URL = "https://api.resend.com/emails"


@dataclass(frozen=True)
class OutgoingEmail:
    """配信先と表示内容を保持する。本人確認リンクはreprへ含めない。"""

    to: str
    subject: str
    text: str = field(repr=False)
    html: str = field(repr=False)
    idempotency_key: str


class EmailService:
    """送信結果を共通化し、配信側の秘密を含むエラーを公開しない。"""

    def __init__(self, http_transport: httpx.BaseTransport | None = None) -> None:
        """HTTP transportを任意に差し替え可能にする。

        Args:
            http_transport: テスト用などのHTTP transport。通常は既定を使う。
        """
        self._http_transport = http_transport

    def ensure_available(self) -> None:
        """送信前にproviderと必須設定を確認する。

        Raises:
            EmailUnavailableError: providerが無効、または送信設定が不足する場合。
        """
        try:
            settings.validate_production()
        except (RuntimeError, ValueError):
            raise EmailUnavailableError() from None
        if settings.email_provider not in {"smtp", "resend"}:
            raise EmailUnavailableError()
        self._sender_address()
        if not 1 <= settings.email_timeout_seconds <= 30:
            raise EmailUnavailableError()
        if settings.email_provider == "resend":
            if not settings.resend_api_key.strip():
                raise EmailUnavailableError()
        elif (
            not settings.smtp_host.strip()
            or not 1 <= settings.smtp_port <= 65535
            or bool(settings.smtp_username) != bool(settings.smtp_password)
        ):
            raise EmailUnavailableError()

    def send(self, message: OutgoingEmail) -> str:
        """メールを1人に送信し、providerの受付IDを返す。

        Args:
            message: 配信先、表示内容、再送時に固定する冪等性キー。

        Returns:
            Resendの受付ID、またはSMTPのMessage-ID。

        Raises:
            EmailUnavailableError: 配信が受け付けられなかった場合。
        """
        self.ensure_available()
        self._validate_message(message)
        if settings.email_provider == "resend":
            return self._send_resend(message)
        return self._send_smtp(message)

    @staticmethod
    def _sender_address() -> str:
        """単一の送信元アドレスを取得し、ヘッダー挿入を拒否する。"""
        if not settings.email_from.strip() or any(
            separator in settings.email_from for separator in ("\r", "\n")
        ):
            raise EmailUnavailableError()
        try:
            addresses = getaddresses([settings.email_from])
            if len(addresses) != 1:
                raise ValueError
            _, address = addresses[0]
            parsed = Address(addr_spec=address)
            if not parsed.username or not parsed.domain:
                raise ValueError
        except ValueError:
            raise EmailUnavailableError() from None
        return address

    @staticmethod
    def _validate_message(message: OutgoingEmail) -> None:
        """宛先とヘッダーを検証し、複数人への配送を拒否する。"""
        if (
            any(
                separator in value
                for value in (message.to, message.subject, message.idempotency_key)
                for separator in ("\r", "\n")
            )
            or not 1 <= len(message.idempotency_key) <= 256
        ):
            raise EmailUnavailableError()
        try:
            recipient = Address(addr_spec=message.to)
            if not recipient.username or not recipient.domain:
                raise ValueError
        except ValueError:
            raise EmailUnavailableError() from None

    def _send_resend(self, message: OutgoingEmail) -> str:
        """APIへ送信する。HTTPエラー本文や元例外の詳細は保持しない。"""
        try:
            with httpx.Client(
                timeout=settings.email_timeout_seconds,
                follow_redirects=False,
                transport=self._http_transport,
            ) as client:
                response = client.post(
                    RESEND_EMAILS_URL,
                    headers={
                        "Authorization": f"Bearer {settings.resend_api_key}",
                        "Idempotency-Key": message.idempotency_key,
                    },
                    json={
                        "from": settings.email_from,
                        "to": [message.to],
                        "subject": message.subject,
                        "text": message.text,
                        "html": message.html,
                    },
                )
                if not 200 <= response.status_code < 300:
                    raise EmailUnavailableError()
                result = response.json()
                if not isinstance(result, dict):
                    raise EmailUnavailableError()
                message_id = result.get("id")
                if not isinstance(message_id, str) or not message_id:
                    raise EmailUnavailableError()
                return message_id
        except (httpx.HTTPError, ValueError):
            raise EmailUnavailableError() from None

    def _send_smtp(self, message: OutgoingEmail) -> str:
        """SMTPでmultipartメールを送り、拒否された宛先があれば失敗する。"""
        sender = self._sender_address()
        digest = hashlib.sha256(message.idempotency_key.encode()).hexdigest()
        message_id = f"<{digest}@{sender.rpartition('@')[2]}>"
        try:
            email = EmailMessage()
            email["From"] = settings.email_from
            email["To"] = message.to
            email["Subject"] = message.subject
            email["Message-ID"] = message_id
            email.set_content(message.text)
            email.add_alternative(message.html, subtype="html")
            with smtplib.SMTP(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.email_timeout_seconds,
            ) as client:
                if settings.smtp_starttls:
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                if settings.smtp_username:
                    client.login(settings.smtp_username, settings.smtp_password)
                refused = client.send_message(
                    email,
                    from_addr=sender,
                    to_addrs=[message.to],
                )
                if refused:
                    raise EmailUnavailableError()
        except (smtplib.SMTPException, OSError, ValueError):
            raise EmailUnavailableError() from None
        return message_id
