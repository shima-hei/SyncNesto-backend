"""公開APIに対するinstance共通のリクエスト制限。"""

import hashlib
import hmac

from app.core.config import settings
from app.db.session import session_local
from app.repositories.request_limit import RequestLimitRepository


def consume_request_budget(client_ip: str, login: bool) -> int:
    """IPを保存せず、用途別上限を短い独立トランザクションで確認する。"""
    scope = "login" if login else "api"
    digest = hmac.new(
        settings.secret_key.encode(), client_ip.encode(), hashlib.sha256
    ).hexdigest()
    with session_local() as db:
        return RequestLimitRepository(db).consume(
            f"{scope}:{digest}", 10 if login else 240
        )
