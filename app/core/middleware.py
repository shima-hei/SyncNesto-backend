from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings
from app.core.csrf import (
    build_csrf_error_response,
    is_valid_csrf_request,
    should_check_csrf,
)
from app.core.logging import (
    reset_request_id,
    reset_request_metadata,
    set_request_id,
    set_request_metadata,
)


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
        request_id_token = set_request_id(request_id)
        client_ip_token, user_agent_token = set_request_metadata(
            client_ip=request.client.host if request.client is not None else None,
            user_agent=request.headers.get("User-Agent"),
        )

        try:
            response = await call_next(request)
        finally:
            reset_request_id(request_id_token)
            reset_request_metadata(client_ip_token, user_agent_token)

        response.headers["X-Request-ID"] = request_id
        return response


class CsrfMiddleware(BaseHTTPMiddleware):
    """更新系リクエストのCSRF tokenを検証するMiddleware。"""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """CSRF検証を行い、問題なければ後続処理へ渡す。

        Args:
            request: 受信したHTTPリクエスト。
            call_next: 次のMiddlewareまたはエンドポイントを呼び出す関数。

        Returns:
            HTTPレスポンス。
        """
        if should_check_csrf(request) and not is_valid_csrf_request(request):
            return build_csrf_error_response()

        return await call_next(request)


def register_middleware(app: FastAPI) -> None:
    """アプリケーション共通のMiddlewareを登録する。

    Args:
        app: Middlewareを登録するFastAPIアプリケーション。
    """
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
