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


class PasswordChangeRequiredError(ForbiddenError):
    """本人の初回パスワード設定が必要な場合の例外。"""

    message = error_messages.PASSWORD_CHANGE_REQUIRED
    code = "PASSWORD_CHANGE_REQUIRED"


class InitialPasswordExpiredError(ForbiddenError):
    """初回パスワードの期限切れを通常のセッション失効と区別する。"""

    message = error_messages.INITIAL_PASSWORD_EXPIRED
    code = "INITIAL_PASSWORD_EXPIRED"


class InitialPasswordReuseError(BadRequestError):
    """本人の設定で初回パスワードを再使用する操作を拒否する。"""

    message = error_messages.INITIAL_PASSWORD_REUSE
    code = "INITIAL_PASSWORD_REUSE"


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


class LastProjectAdminRequiredError(ConflictError):
    """プロジェクト管理者が0人になる操作を拒否する例外。"""

    message = error_messages.LAST_PROJECT_ADMIN_REQUIRED
    code = "LAST_PROJECT_ADMIN_REQUIRED"


class InvalidCredentialsError(UnauthorizedError):
    """ログイン認証情報が正しくない場合の例外。"""

    message = error_messages.INVALID_CREDENTIALS
    code = "INVALID_CREDENTIALS"


class EmailAlreadyRegisteredError(BadRequestError):
    """指定されたメールアドレスが既に登録済みの場合の例外。"""

    message = error_messages.EMAIL_ALREADY_REGISTERED
    code = "EMAIL_ALREADY_REGISTERED"


class EmailUnavailableError(AppError):
    """メール設定が未完了、または配信APIが受け付けなかった場合の例外。"""

    message = error_messages.EMAIL_UNAVAILABLE
    code = "EMAIL_UNAVAILABLE"


class AccountActionInvalidError(BadRequestError):
    """本人確認リンクが無効な場合の例外。ログインJWTとは区別する。"""

    message = error_messages.ACCOUNT_ACTION_INVALID
    code = "ACCOUNT_ACTION_INVALID"


class AccountActionRateLimitedError(AppError):
    """本人確認メールの申請回数が上限に達した場合の例外。"""

    message = error_messages.ACCOUNT_ACTION_RATE_LIMITED
    code = "RATE_LIMITED"
