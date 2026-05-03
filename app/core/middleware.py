from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings
from app.core.logging import reset_request_id, set_request_id


class RequestIdMiddleware(BaseHTTPMiddleware):
    """リクエストごとにrequest idを設定するMiddleware。"""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """request idをログコンテキストとレスポンスヘッダーへ設定する。

        Args:
            request: 受信したHTTPリクエスト。
            call_next: 次のMiddlewareまたはエンドポイントを呼び出す関数。

        Returns:
            X-Request-IDヘッダーを付与したHTTPレスポンス。
        """
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        token = set_request_id(request_id)

        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)

        response.headers["X-Request-ID"] = request_id
        return response


def register_middleware(app: FastAPI) -> None:
    """アプリケーション共通のMiddlewareを登録する。

    Args:
        app: Middlewareを登録するFastAPIアプリケーション。
    """
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
