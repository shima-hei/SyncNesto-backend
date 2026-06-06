"""アプリケーション独自例外を定義するモジュール。"""

from app.core import error_messages


class AppError(Exception):
    """アプリケーション独自例外の基底クラス。"""

    message = error_messages.APPLICATION_ERROR
    code = "APP_ERROR"

    def __init__(self, message: str | None = None) -> None:
        """アプリケーション独自例外を初期化する。

        Args:
            message: 例外に設定する業務エラーメッセージ。
        """
        self.message = message or self.message
        super().__init__(self.message)


class BadRequestError(AppError):
    """リクエスト内容が不正な場合の例外。"""

    message = error_messages.BAD_REQUEST
    code = "BAD_REQUEST"


class UnauthorizedError(AppError):
    """認証が必要、または認証情報が正しくない場合の例外。"""

    message = error_messages.UNAUTHORIZED
    code = "UNAUTHORIZED"


class AuthenticationRequiredError(UnauthorizedError):
    """認証トークンが存在しない場合の例外。"""

    message = error_messages.AUTHENTICATION_REQUIRED
    code = "AUTHENTICATION_REQUIRED"


class TokenExpiredError(UnauthorizedError):
    """認証トークンの有効期限が切れている場合の例外。"""

    message = error_messages.TOKEN_EXPIRED
    code = "TOKEN_EXPIRED"


class InvalidTokenError(UnauthorizedError):
    """認証トークンが不正な場合の例外。"""

    message = error_messages.INVALID_TOKEN
    code = "INVALID_TOKEN"


class ForbiddenError(AppError):
    """操作権限がない場合の例外。"""

    message = error_messages.FORBIDDEN
    code = "FORBIDDEN"


class CsrfTokenInvalidError(ForbiddenError):
    """CSRF tokenが不正な場合の例外。"""

    message = error_messages.INVALID_CSRF_TOKEN
    code = "CSRF_TOKEN_INVALID"


class NotFoundError(AppError):
    """対象リソースが存在しない場合の例外。"""

    message = error_messages.NOT_FOUND
    code = "NOT_FOUND"


class ConflictError(AppError):
    """リソースの状態が競合している場合の例外。"""

    message = error_messages.CONFLICT
    code = "CONFLICT"


class DuplicateResourceError(ConflictError):
    """一意であるべきリソースが既に存在する場合の例外。"""

    message = error_messages.DUPLICATE_RESOURCE
    code = "DUPLICATE_RESOURCE"


class VersionConflictError(ConflictError):
    """更新対象のバージョンが最新ではない場合の例外。"""

    message = error_messages.VERSION_CONFLICT
    code = "VERSION_CONFLICT"

    def __init__(self, current: dict[str, object], message: str | None = None) -> None:
        """バージョン競合例外を初期化する。

        Args:
            current: DBに保存されている最新のリソース情報。
            message: 例外に設定する業務エラーメッセージ。
        """
        self.current = current
        super().__init__(message)


class InvalidCredentialsError(UnauthorizedError):
    """ログイン認証情報が正しくない場合の例外。"""

    message = error_messages.INVALID_CREDENTIALS
    code = "INVALID_CREDENTIALS"


class EmailAlreadyRegisteredError(BadRequestError):
    """指定されたメールアドレスが既に登録済みの場合の例外。"""

    message = error_messages.EMAIL_ALREADY_REGISTERED
    code = "EMAIL_ALREADY_REGISTERED"
