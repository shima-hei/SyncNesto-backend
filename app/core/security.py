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
) -> str:
    """アクセストークンを作成する。

    Args:
        subject: トークンの主体を表す値。
        session_id: セッションID。
        expires_at: トークンの有効期限。未指定の場合は設定値から算出する。

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

    return jwt.encode(
        payload,
        settings.secret_key,
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
    return jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.algorithm],
        options={"verify_exp": verify_exp},
    )
