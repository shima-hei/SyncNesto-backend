"""公開APIのBFF認証と、業務処理前の共有リクエスト制限。"""

import hmac
from ipaddress import ip_address
from math import ceil
from time import monotonic

from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.services.request_limit import consume_request_budget

BFF_KEY_HEADER = "X-Syncnesto-BFF-Key"
CLIENT_IP_HEADER = "X-Syncnesto-Client-IP"


def trusted_client_ip(request: Request) -> str:
    """共有キー検証済みBFFのIPだけを使い、外部からの偽装ヘッダーを信用しない。"""
    if settings.bff_shared_secret and hmac.compare_digest(
        request.headers.get(BFF_KEY_HEADER, "").encode(),
        settings.bff_shared_secret.encode(),
    ):
        try:
            return str(ip_address(request.headers.get(CLIENT_IP_HEADER, "")))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


class IngressMiddleware(BaseHTTPMiddleware):
    """BFF以外の直接アクセスを拒否し、DBとプロセス内で回数を制限する。"""

    window_seconds = 60
    max_buckets = 10000

    def __init__(self, app) -> None:
        """IP・用途ごとの有限なカウンターを準備する。"""
        super().__init__(app)
        self.buckets: dict[tuple[str, bool], tuple[float, int]] = {}

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """健康確認以外は共有キーを確認してから後続処理へ渡す。"""
        if request.url.path == "/" and request.method in {"GET", "HEAD"}:
            return await call_next(request)
        if not settings.bff_shared_secret:
            return await call_next(request)
        supplied = request.headers.get(BFF_KEY_HEADER, "")
        if not hmac.compare_digest(
            supplied.encode(), settings.bff_shared_secret.encode()
        ):
            return JSONResponse(
                {"message": "Forbidden", "code": "FORBIDDEN"}, status_code=403
            )
        if settings.app_env != "production":
            return await call_next(request)

        client_ip = trusted_client_ip(request)
        login = request.url.path.rstrip("/") == "/auth/login"
        key = (client_ip, login)
        now = monotonic()
        start, count = self.buckets.get(key, (now, 0))
        if now - start >= self.window_seconds:
            start, count = now, 0
        if key not in self.buckets and len(self.buckets) >= self.max_buckets:
            self.buckets = {
                k: value
                for k, value in self.buckets.items()
                if now - value[0] < self.window_seconds
            }
            if len(self.buckets) >= self.max_buckets:
                return self.limited_response(self.window_seconds)
        if count >= (10 if login else 240):
            return self.limited_response(ceil(self.window_seconds - (now - start)))
        self.buckets[key] = (start, count + 1)
        try:
            retry_after = await run_in_threadpool(
                consume_request_budget, client_ip, login
            )
        except SQLAlchemyError:
            return JSONResponse(
                {"message": "Service unavailable", "code": "SERVICE_UNAVAILABLE"},
                status_code=503,
            )
        if retry_after:
            return self.limited_response(retry_after)
        return await call_next(request)

    @staticmethod
    def limited_response(retry_after: int) -> Response:
        """429に再試行までの秒数を付ける。"""
        return JSONResponse(
            {"message": "Too many requests", "code": "RATE_LIMITED"},
            status_code=429,
            headers={"Retry-After": str(max(1, retry_after))},
        )
