"""アプリケーション共通の例外ハンドラーを定義するモジュール。"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    AppError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
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


def register_exception_handlers(app: FastAPI) -> None:
    """アプリケーション共通の例外ハンドラーを登録する。

    Args:
        app: 例外ハンドラーを登録するFastAPIアプリケーション。
    """

    @app.exception_handler(AppError)
    async def app_error_handler(
        _request: Request,
        exc: AppError,
    ) -> JSONResponse:
        """アプリケーション独自例外をHTTPレスポンスへ変換する。

        Args:
            _request: 例外が発生したリクエスト。
            exc: 発生したアプリケーション独自例外。

        Returns:
            JSON形式のエラーレスポンス。
        """
        status_code = get_status_code(exc)
        if status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            logger.error(
                "Unhandled application error: type=%s message=%s",
                type(exc).__name__,
                exc.message,
            )

        return JSONResponse(
            status_code=status_code,
            content={"detail": exc.message},
        )
