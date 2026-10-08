"""
パスワードハッシュ化など、認証関連の共通処理を提供するモジュール。
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

password_hash = PasswordHash.recommended()
DEMO_AUDIENCE = "syncnesto-demo"


def get_password_hash(password: str) -> str:
    """平文パスワードをハッシュ化する。

    Args:
        password: ハッシュ化する平文パスワード。

    Returns:
        ハッシュ化されたパスワード文字列。
    """
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """平文パスワードとハッシュ済みパスワードを照合する。

    Args:
        plain_password: ユーザーが入力した平文パスワード。
        hashed_password: DBに保存されているハッシュ済みパスワード。

    Returns:
        パスワードが一致する場合はTrue、一致しない場合はFalse。
    """
    return password_hash.verify(plain_password, hashed_password)


def create_access_token(
    subject: str,
    session_id: UUID | str | None = None,
    expires_at: datetime | None = None,
    password_setup_only: bool = False,
    demo: bool = False,
) -> str:
    """アクセストークンを作成する。

    Args:
        subject: トークンの主体を表す値。
        session_id: セッションID。
        expires_at: トークンの有効期限。未指定の場合は設定値から算出する。
        password_setup_only: 初回設定専用のログインであることを固定する。

    Returns:
        JWTアクセストークン。
    """
    if expires_at is None:
        expires_at = datetime.now(UTC) + timedelta(
            minutes=settings.access_token_expire_minutes
        )

    payload = {
        "sub": subject,
        "exp": expires_at,
        "iat": datetime.now(UTC),
    }
    if session_id is not None:
        payload["sid"] = str(session_id)
    if password_setup_only:
        payload["password_setup_only"] = True
    if demo:
        if (
            not settings.demo_secret_key
            or settings.demo_secret_key == settings.secret_key
        ):
            raise RuntimeError("A separate DEMO_SECRET_KEY is required")
        payload["aud"] = DEMO_AUDIENCE

    return jwt.encode(
        payload,
        settings.demo_secret_key if demo else settings.secret_key,
        algorithm=settings.algorithm,
    )


def decode_access_token(token: str, verify_exp: bool = True) -> dict[str, Any]:
    """アクセストークンをデコードする。

    Args:
        token: JWTアクセストークン。
        verify_exp: expを検証するか。

    Returns:
        デコードされたJWT payload。
    """
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
            options={"verify_exp": verify_exp},
        )
    except jwt.InvalidSignatureError:
        if (
            not settings.demo_secret_key
            or settings.demo_secret_key == settings.secret_key
        ):
            raise
        return jwt.decode(
            token,
            settings.demo_secret_key,
            algorithms=[settings.algorithm],
            audience=DEMO_AUDIENCE,
            options={
                "verify_exp": verify_exp,
                "require": ["aud", "sub", "sid", "exp", "iat"],
                "strict_aud": True,
            },
        )
