"""アプリケーション独自例外を定義するモジュール。"""


class AppError(Exception):
    """アプリケーション独自例外の基底クラス。"""


class EmailAlreadyRegisteredError(AppError):
    """指定されたメールアドレスが既に登録済みの場合の例外。"""
