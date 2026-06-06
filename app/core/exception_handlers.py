"""アプリケーション共通の例外ハンドラーを定義するモジュール。"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core.auth_cookie import delete_auth_cookie
from app.core.config import settings
from app.core.csrf import delete_csrf_cookie
from app.core.exceptions import (
    AppError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    InvalidTokenError,
    NotFoundError,
    TokenExpiredError,
    UnauthorizedError,
    VersionConflictError,
)

logger = logging.getLogger(__name__)

ERROR_STATUS_MAP: dict[type[AppError], int] = {
    BadRequestError: status.HTTP_400_BAD_REQUEST,
    UnauthorizedError: status.HTTP_401_UNAUTHORIZED,
    ForbiddenError: status.HTTP_403_FORBIDDEN,
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
}


def get_status_code(exc: AppError) -> int:
    """アプリケーション独自例外に対応するHTTPステータスコードを取得する。

    Args:
        exc: 発生したアプリケーション独自例外。

    Returns:
        例外に対応するHTTPステータスコード。
    """
    for error_type in type(exc).mro():
        status_code = ERROR_STATUS_MAP.get(error_type)
        if status_code is not None:
            return status_code

    return status.HTTP_500_INTERNAL_SERVER_ERROR


def should_delete_auth_cookie(request: Request, exc: AppError) -> bool:
    """認証Cookieを削除すべきか判定する。

    Args:
        request: 例外が発生したリクエスト。
        exc: 発生したアプリケーション独自例外。

    Returns:
        認証Cookieを削除すべき場合はTrue。
    """
    return (
        settings.auth_cookie_name in request.cookies
        and isinstance(exc, TokenExpiredError | InvalidTokenError)
    )


def register_exception_handlers(app: FastAPI) -> None:
    """アプリケーション共通の例外ハンドラーを登録する。

    Args:
        app: 例外ハンドラーを登録するFastAPIアプリケーション。
    """

    @app.exception_handler(AppError)
    async def app_error_handler(
        request: Request,
        exc: AppError,
    ) -> JSONResponse:
        """アプリケーション独自例外をHTTPレスポンスへ変換する。

        Args:
            request: 例外が発生したリクエスト。
            exc: 発生したアプリケーション独自例外。

        Returns:
            JSON形式のエラーレスポンス。
        """
        status_code = get_status_code(exc)
        if status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            logger.error(
                "Unhandled application error: type=%s code=%s message=%s",
                type(exc).__name__,
                exc.code,
                exc.message,
            )

        content: dict[str, object] = {
            "message": exc.message,
            "code": exc.code,
        }
        if isinstance(exc, VersionConflictError):
            content["current"] = jsonable_encoder(exc.current)

        response = JSONResponse(status_code=status_code, content=content)
        if should_delete_auth_cookie(request, exc):
            delete_auth_cookie(response)
            delete_csrf_cookie(response)

        return response
