"""
ユーザーに関するPydanticスキーマを定義するモジュール。

リクエスト（作成）およびレスポンス（読み取り）で使用するデータ構造を定義する。
"""

from pydantic import BaseModel, EmailStr


class UserBase(BaseModel):
    """
    ユーザーschemaの共通フィールドを定義する基底schema
    """

    email: EmailStr
    name: str


class UserCreate(UserBase):
    """
    ユーザー登録リクエストで受け取るschema。
    password は平文で受け取るが、DBには保存せず hashed_password に変換して保存する。
    """

    password: str


class UserRead(UserBase):
    """
    ユーザー読み取り時に返すschema。
    """

    id: int

    model_config = {"from_attributes": True}
