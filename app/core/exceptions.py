"""アプリケーション独自例外を定義するモジュール。"""


class AppError(Exception):
    """アプリケーション独自例外の基底クラス。"""

    message = "Application error"
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

    message = "Bad request"
    code = "BAD_REQUEST"


class UnauthorizedError(AppError):
    """認証が必要、または認証情報が正しくない場合の例外。"""

    message = "Unauthorized"
    code = "UNAUTHORIZED"


class ForbiddenError(AppError):
    """操作権限がない場合の例外。"""

    message = "Forbidden"
    code = "FORBIDDEN"


class NotFoundError(AppError):
    """対象リソースが存在しない場合の例外。"""

    message = "Not found"
    code = "NOT_FOUND"


class ConflictError(AppError):
    """リソースの状態が競合している場合の例外。"""

    message = "Conflict"
    code = "CONFLICT"


class InvalidCredentialsError(UnauthorizedError):
    """ログイン認証情報が正しくない場合の例外。"""

    message = "Invalid email or password"
    code = "INVALID_CREDENTIALS"


class EmailAlreadyRegisteredError(BadRequestError):
    """指定されたメールアドレスが既に登録済みの場合の例外。"""

    message = "Email already registered"
    code = "EMAIL_ALREADY_REGISTERED"
