"""CSRF tokenの生成、Cookie操作、検証処理を定義するモジュール。"""

from hmac import compare_digest
from secrets import token_urlsafe

from fastapi import Response, status
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.core.config import settings
from app.core.exceptions import CsrfTokenInvalidError

CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
CSRF_EXEMPT_PATHS = {"/auth/login"}
CSRF_TOKEN_BYTES = 32


def generate_csrf_token() -> str:
    """CSRF tokenを生成する。

    Returns:
        URL-safeなCSRF token。
    """
    return token_urlsafe(CSRF_TOKEN_BYTES)


def set_csrf_cookie(response: Response, token: str) -> None:
    """CSRF token Cookieを設定する。

    Args:
        response: HTTPレスポンス。
        token: Cookieへ保存するCSRF token。
    """
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=token,
        httponly=False,
        secure=settings.csrf_cookie_secure,
        samesite=settings.csrf_cookie_samesite,
        path="/",
    )


def delete_csrf_cookie(response: Response) -> None:
    """CSRF token Cookieを削除する。

    Args:
        response: HTTPレスポンス。
    """
    response.delete_cookie(
        key=settings.csrf_cookie_name,
        secure=settings.csrf_cookie_secure,
        samesite=settings.csrf_cookie_samesite,
        path="/",
    )


def should_check_csrf(request: Request) -> bool:
    """CSRF検証が必要なリクエストか判定する。

    Args:
        request: 受信したHTTPリクエスト。

    Returns:
        CSRF検証が必要な場合はTrue。
    """
    return (
        request.method.upper() not in CSRF_SAFE_METHODS
        and request.url.path not in CSRF_EXEMPT_PATHS
        and settings.auth_cookie_name in request.cookies
    )


def is_valid_csrf_request(request: Request) -> bool:
    """CSRF Cookieとリクエストヘッダーが一致するか検証する。

    Args:
        request: 受信したHTTPリクエスト。

    Returns:
        CSRF tokenが有効な場合はTrue。
    """
    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    header_token = request.headers.get(settings.csrf_header_name)

    if cookie_token is None or header_token is None:
        return False

    return compare_digest(cookie_token, header_token)


def build_csrf_error_response() -> JSONResponse:
    """CSRF検証エラーレスポンスを作成する。

    Returns:
        403のJSONエラーレスポンス。
    """
    error = CsrfTokenInvalidError()
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={
            "message": error.message,
            "code": error.code,
        },
    )
