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


class ArtifactReleaseTooLargeError(AppError):
    """本文を切り捨てず、容量超過を明示する。"""

    message = error_messages.ARTIFACT_TOO_LARGE
    code = "ARTIFACT_RELEASE_TOO_LARGE"


class DemoLimitError(AppError):
    """一時環境の共有容量・発行上限を超えた。"""

    message = "デモの利用上限に達しました。しばらく待って再度お試しください"
    code = "DEMO_LIMIT_REACHED"


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


class MfaRequiredError(ForbiddenError):
    """通常利用には追加認証が必要。"""

    message = error_messages.MFA_REQUIRED
    code = "MFA_REQUIRED"


class MfaInvalidError(BadRequestError):
    """追加認証の要求または応答が無効。"""

    message = error_messages.MFA_INVALID
    code = "MFA_INVALID"


class MfaUnavailableError(AppError):
    """追加認証の暗号鍵などが未設定。"""

    message = error_messages.MFA_UNAVAILABLE
    code = "MFA_UNAVAILABLE"


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


class SpecificationUnresolvedCommentsError(ConflictError):
    """設計書の未解決指摘が承認を妨げている。"""

    message = error_messages.SPECIFICATION_UNRESOLVED_COMMENTS
    code = "SPECIFICATION_UNRESOLVED_COMMENTS"


class SpecificationReviewFieldsError(BadRequestError):
    """確認依頼・承認に必要な設計項目が不足している。"""

    message = error_messages.SPECIFICATION_REVIEW_FIELDS_REQUIRED
    code = "SPECIFICATION_REVIEW_FIELDS_REQUIRED"


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
