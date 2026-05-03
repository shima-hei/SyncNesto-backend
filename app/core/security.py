"""
パスワードハッシュ化など、認証関連の共通処理を提供するモジュール。
"""

from pwdlib import PasswordHash

password_hash = PasswordHash.recommended()


def get_password_hash(password: str) -> str:
    """平文パスワードをハッシュ化する。

    Args:
        password: ハッシュ化する平文パスワード。

    Returns:
        ハッシュ化されたパスワード文字列。
    """
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """平文パスワードとハッシュ済みパスワードを照合する。

    Args:
        plain_password: ユーザーが入力した平文パスワード。
        hashed_password: DBに保存されているハッシュ済みパスワード。

    Returns:
        パスワードが一致する場合はTrue、一致しない場合はFalse。
    """
    return password_hash.verify(plain_password, hashed_password)
