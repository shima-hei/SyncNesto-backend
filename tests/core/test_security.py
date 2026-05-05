"""securityモジュールのテスト。"""

from app.core.security import (
    create_access_token,
    decode_access_token,
    get_password_hash,
    verify_password,
)


def test_get_password_hash_returns_different_value_from_plain_password() -> None:
    """ハッシュ化後の値が平文パスワードと異なることを確認する。"""
    plain_password = "password123"

    hashed_password = get_password_hash(plain_password)

    assert hashed_password != plain_password


def test_get_password_hash_returns_argon2id_hash() -> None:
    """ハッシュ化後の値がArgon2id形式であることを確認する。"""
    hashed_password = get_password_hash("password123")

    assert hashed_password.startswith("$argon2id$")


def test_verify_password_returns_true_for_correct_password() -> None:
    """正しいパスワードの場合に照合が成功することを確認する。"""
    plain_password = "password123"
    hashed_password = get_password_hash(plain_password)

    assert verify_password(plain_password, hashed_password)


def test_verify_password_returns_false_for_wrong_password() -> None:
    """誤ったパスワードの場合に照合が失敗することを確認する。"""
    hashed_password = get_password_hash("password123")

    assert not verify_password("wrong-password", hashed_password)


def test_create_access_token_returns_token_string() -> None:
    """アクセストークンを文字列として作成できることを確認する。"""
    token = create_access_token(subject="user@example.com")

    assert isinstance(token, str)
    assert token


def test_decode_access_token_returns_subject() -> None:
    """アクセストークンからsubjectを取得できることを確認する。"""
    token = create_access_token(subject="user@example.com")

    payload = decode_access_token(token)

    assert payload["sub"] == "user@example.com"
    assert "exp" in payload
