"""認証Cookie操作を定義するモジュール。"""

from fastapi import Response

from app.core.config import settings


def set_auth_cookie(response: Response, token: str, max_age_seconds: int) -> None:
    """認証Cookieを設定する。

    Args:
        response: HTTPレスポンス。
        token: Cookieへ保存するアクセストークン。
        max_age_seconds: Cookieの有効秒数。
    """
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        max_age=max_age_seconds,
        path="/",
    )


def delete_auth_cookie(response: Response) -> None:
    """認証Cookieを削除する。

    Args:
        response: HTTPレスポンス。
    """
    response.delete_cookie(
        key=settings.auth_cookie_name,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )
