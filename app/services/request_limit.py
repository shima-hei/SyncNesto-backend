"""公開APIに対するinstance共通のリクエスト制限。"""

import hashlib
import hmac

from app.core.config import settings
from app.core.exceptions import AccountActionRateLimitedError
from app.db.session import demo_session_local, session_local
from app.repositories.request_limit import RequestLimitRepository


def consume_request_budget(client_ip: str, login: bool, demo: bool = False) -> int:
    """IPを保存せず、用途別上限を短い独立トランザクションで確認する。"""
    scope = "login" if login else "api"
    digest = hmac.new(
        (settings.demo_secret_key if demo else settings.secret_key).encode(),
        client_ip.encode(),
        hashlib.sha256,
    ).hexdigest()
    with demo_session_local() if demo else session_local() as db:
        return RequestLimitRepository(db).consume(
            f"{scope}:{digest}", 10 if login else 240
        )


def consume_email_request_budget(identifier: str, client_ip: str) -> None:
    """送信申請を宛先ごと2回/分・IPごと5回/分に制限する。

    登録有無の検索より前に同じ処理を行い、識別子とIPはHMACだけ保存する。
    """
    for scope, value, limit in (
        ("email-recipient", identifier.lower(), 2),
        ("email-ip", client_ip, 5),
    ):
        digest = hmac.new(
            settings.secret_key.encode(), value.encode(), hashlib.sha256
        ).hexdigest()
        with session_local() as db:
            if RequestLimitRepository(db).consume(f"{scope}:{digest}", limit):
                raise AccountActionRateLimitedError()
