"""既存SMTP/Resend設定を使う、期限付き承認通知adapter。"""

import hashlib
import multiprocessing
import signal
import smtplib
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import getaddresses

from app.core.config import settings
from app.services.email import EmailService, OutgoingEmail


@dataclass(frozen=True)
class MailOutcome:
    """SMTPの生応答を含めず、受付と到達を区別する。"""

    outcome: str
    code: str
    message_id: str | None = None


def smtp_exchange(
    message: OutgoingEmail,
    sender: str,
    smtp_factory: Callable[..., smtplib.SMTP] = smtplib.SMTP,
    expires_at=None,
    receipt=None,
) -> MailOutcome:
    """DATA前の拒否とDATA後の結果不明を分類する。"""
    client = None
    data_started = False
    try:
        EmailService._validate_message(message)
        address = Address(addr_spec=getaddresses([sender])[0][1])
        if (
            not address.username
            or not address.domain
            or "\n" in sender
            or "\r" in sender
        ):
            return MailOutcome("failed", "invalid_sender")
        digest = hashlib.sha256(message.idempotency_key.encode()).hexdigest()
        identity = f"<{digest}@example.com>"
        email = EmailMessage(policy=SMTP_POLICY)
        email["From"], email["To"], email["Subject"], email["Message-ID"] = (
            sender,
            message.to,
            message.subject,
            identity,
        )
        email.set_content(message.text, cte="quoted-printable")
        email.add_alternative(message.html, subtype="html", cte="quoted-printable")
        client = smtp_factory("127.0.0.1", 1025, timeout=3)
        for command in (
            client.ehlo,
            lambda: client.mail(str(address)),
            lambda: client.rcpt(message.to),
        ):
            code, _ = command()
            if code >= 400:
                return MailOutcome(
                    "temporary" if code < 500 else "failed", f"smtp_{code}"
                )
        if expires_at is not None and time.time() >= expires_at:
            return MailOutcome("suppressed", "authorization_expired")
        data_started = True
        code, _ = client.data(email.as_bytes())
        if 200 <= code < 300:
            result = MailOutcome("accepted", "smtp_accepted", identity)
            if receipt:
                receipt(result)
            return result
        return MailOutcome(
            "temporary" if 400 <= code < 500 else "failed", f"smtp_{code}"
        )
    except smtplib.SMTPResponseException as error:
        return MailOutcome(
            "temporary" if 400 <= error.smtp_code < 500 else "failed",
            f"smtp_{error.smtp_code}",
        )
    except (smtplib.SMTPException, OSError, TimeoutError):
        return MailOutcome(
            "unknown" if data_started else "temporary",
            "connection_uncertain" if data_started else "connection_unavailable",
        )
    except Exception:
        return MailOutcome("unknown" if data_started else "failed", "transport_error")
    finally:
        if client:
            try:
                client.quit()
            except Exception:
                client.close()


def _capture_child(pipe, message, sender, expires_at):
    """親の終了後も無期限に送らないよう独立deadlineを持つ。"""

    def expired(signum, frame):
        raise TimeoutError

    signal.signal(signal.SIGALRM, expired)
    remaining = min(9, expires_at - time.time())
    if remaining <= 0:
        pipe.send(MailOutcome("suppressed", "authorization_expired"))
        pipe.close()
        return
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        if settings.is_public_environment and settings.email_provider == "resend":
            identity = EmailService().send(message)
            pipe.send(MailOutcome("accepted", "provider_accepted", identity))
            return
        pipe.send(
            smtp_exchange(
                message,
                sender,
                expires_at=expires_at,
                receipt=pipe.send,
                smtp_factory=configured_smtp
                if settings.is_public_environment
                else smtplib.SMTP,
            )
        )
    except Exception:
        pipe.send(MailOutcome("unknown", "transport_uncertain"))
    finally:
        pipe.close()


def configured_smtp(_host, _port, *, timeout):
    """本番の既存SMTP設定でTLSと認証を行い、捕捉先へ切り替えない。"""
    EmailService().ensure_available()
    client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout)
    try:
        client.ehlo()
        if settings.smtp_starttls:
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        return client
    except Exception:
        client.close()
        raise


class LocalApprovalMailTransport:
    """全体10秒・ロック接続喪失時の停止・process回収を行う。"""

    def send(self, message: OutgoingEmail, guard, expires_at) -> MailOutcome:
        """送信先の既存設定を検証し、providerをfallbackしない。"""
        from sqlalchemy import text

        from app.services.notification_email_preferences import (
            approval_mail_available,
            recipient_delivery_available,
        )

        if not approval_mail_available() or not recipient_delivery_available(
            message.to
        ):
            return MailOutcome("failed", "delivery_unavailable")
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        remaining = min(10, expires_at.timestamp() - time.time())
        if remaining <= 0:
            parent.close()
            child.close()
            return MailOutcome("suppressed", "authorization_expired")
        process = context.Process(
            target=_capture_child,
            args=(
                child,
                message,
                settings.email_from
                if settings.is_public_environment
                else settings.local_email_capture_from,
                expires_at.timestamp(),
            ),
            daemon=True,
        )
        deadline = time.monotonic() + remaining
        result = MailOutcome("unknown", "deadline_or_process_exit")
        try:
            process.start()
            child.close()
            while time.monotonic() < deadline:
                if parent.poll(0.05):
                    value = parent.recv()
                    if isinstance(value, MailOutcome):
                        result = value
                    break
                if not process.is_alive():
                    break
                guard.execute(text("SELECT 1"))
        except Exception:
            result = MailOutcome("unknown", "guard_or_process_unavailable")
        finally:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=0.2)
                if process.is_alive():
                    process.kill()
                    process.join()
            parent.close()
            child.close()
        return result
