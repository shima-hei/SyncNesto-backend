import logging
from time import perf_counter
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
    client_ip_context,
    request_id_context,
    reset_request_id,
    reset_request_metadata,
    set_request_id,
    set_request_metadata,
    user_agent_context,
)

logger = logging.getLogger(__name__)


def build_request_log_extra(
    request: Request,
    *,
    duration_ms: float,
    status_code: int | None = None,
    exception_type: str | None = None,
) -> dict[str, object]:
    """リクエストログ用の構造化フィールドを組み立てる。

    Args:
        request: 受信したHTTPリクエスト。
        duration_ms: 処理時間ミリ秒。
        status_code: HTTPステータスコード。
        exception_type: 例外クラス名。

    Returns:
        logger extraに渡す構造化フィールド。
    """
    is_slow = duration_ms >= settings.slow_request_threshold_ms
    return {
        "method": request.method,
        "path": request.url.path,
        "status_code": status_code,
        "duration_ms": round(duration_ms, 2),
        "client_ip": client_ip_context.get(),
        "user_agent": user_agent_context.get(),
        "request_id": request_id_context.get(),
        "is_slow": is_slow,
        "exception_type": exception_type,
    }


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


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """リクエスト単位の通常ログを出力するMiddleware。"""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """HTTPリクエストの完了または失敗をログ出力する。

        Args:
            request: 受信したHTTPリクエスト。
            call_next: 次のMiddlewareまたはエンドポイントを呼び出す関数。

        Returns:
            HTTPレスポンス。
        """
        started_at = perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = (perf_counter() - started_at) * 1000
            logger.exception(
                "Request failed",
                extra=build_request_log_extra(
                    request,
                    duration_ms=duration_ms,
                    exception_type=exc.__class__.__name__,
                ),
            )
            raise

        duration_ms = (perf_counter() - started_at) * 1000
        extra = build_request_log_extra(
            request,
            duration_ms=duration_ms,
            status_code=response.status_code,
        )
        if extra["is_slow"]:
            logger.warning("Slow request completed", extra=extra)
        else:
            logger.info("Request completed", extra=extra)
        return response


def register_middleware(app: FastAPI) -> None:
    """アプリケーション共通のMiddlewareを登録する。

    Args:
        app: Middlewareを登録するFastAPIアプリケーション。
    """
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
