"""アプリケーション共通の例外ハンドラーを定義するモジュール。"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.exceptions import EmailAlreadyRegisteredError


def register_exception_handlers(app: FastAPI) -> None:
    """アプリケーション共通の例外ハンドラーを登録する。

    Args:
        app: 例外ハンドラーを登録するFastAPIアプリケーション。
    """

    @app.exception_handler(EmailAlreadyRegisteredError)
    async def email_already_registered_handler(
        _request: Request,
        _exc: EmailAlreadyRegisteredError,
    ) -> JSONResponse:
        """メールアドレス重複エラーをHTTPレスポンスへ変換する。

        Args:
            _request: 例外が発生したリクエスト。
            _exc: 発生したメールアドレス重複エラー。

        Returns:
            HTTP 400のJSONレスポンス。
        """
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Email already registered"},
        )
