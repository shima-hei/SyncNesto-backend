"""追加認証の共通Cookieと秘密の検証。"""

import hashlib
from datetime import UTC, datetime
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, MultiFernet
from fastapi import Response

from app.core.config import settings
from app.core.exceptions import MfaRequiredError, MfaUnavailableError
from app.models.session import UserSession
from app.models.user import User

FLOW_SECONDS = 600


def digest(value: str) -> str:
    """高エントロピーtokenを平文保存しない。"""
    return hashlib.sha256(value.encode()).hexdigest()


def fingerprint(user: User) -> str:
    """パスワード・メール・MFA変更前の限定状態を無効にする。"""
    return digest(
        f"{user.id}:{user.email}:{user.hashed_password}:{user.mfa_security_version}:{user.password_change_required}"
    )


def cipher() -> MultiFernet:
    """先頭鍵で暗号化し、旧鍵での復号を許可する。鍵はDBに保存しない。"""
    try:
        keys = [
            Fernet(key.strip().encode())
            for key in settings.mfa_encryption_keys.split(",")
            if key.strip()
        ]
        if not keys:
            raise ValueError()
        return MultiFernet(keys)
    except (ValueError, TypeError):
        raise MfaUnavailableError() from None


def webauthn_settings() -> tuple[str, str]:
    """固定のFrontend originだけを信頼する。"""
    origin = urlsplit(settings.webauthn_origin)
    if (
        origin.hostname != settings.webauthn_rp_id
        or origin.path not in {"", "/"}
        or origin.query
        or origin.fragment
        or origin.username
        or origin.password
        or (
            origin.scheme != "https"
            and not (
                not settings.is_public_environment
                and origin.hostname in {"localhost", "127.0.0.1"}
                and origin.scheme == "http"
            )
        )
        or (
            settings.is_public_environment
            and settings.webauthn_origin.rstrip("/")
            != settings.frontend_public_url.rstrip("/")
        )
    ):
        raise MfaUnavailableError()
    return settings.webauthn_rp_id, settings.webauthn_origin.rstrip("/")


def set_flow_cookie(response: Response, token: str) -> None:
    """限定状態をHttpOnly Cookieへ設定する。"""
    response.set_cookie(
        settings.mfa_cookie_name,
        token,
        max_age=FLOW_SECONDS,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


def delete_flow_cookie(response: Response) -> None:
    """限定状態のCookieを破棄する。"""
    response.delete_cookie(
        settings.mfa_cookie_name,
        path="/",
        secure=settings.auth_cookie_secure,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"


def require_verified(user: User, session: UserSession) -> None:
    """MFAの登録状態ではなく、このセッションの検証結果を要求する。"""
    if settings.mfa_required and (
        user.mfa_recovery_pending
        or not session.mfa_verified_at
        or session.mfa_security_version != user.mfa_security_version
        or session.mfa_method not in {"totp", "passkey", "recovery_code"}
    ):
        raise MfaRequiredError()


def require_recent(session: UserSession) -> None:
    """認証情報の変更には5分以内の追加認証を要求する。"""
    if (
        not session.mfa_reauthenticated_at
        or (datetime.now(UTC) - session.mfa_reauthenticated_at).total_seconds() > 300
    ):
        raise MfaRequiredError()
