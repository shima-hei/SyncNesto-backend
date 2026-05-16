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


class UserUpdate(BaseModel):
    """ユーザー更新リクエストで受け取るschema。"""

    version: int
    email: EmailStr | None = None
    name: str | None = None
    password: str | None = None


class UserRead(UserBase):
    """
    ユーザー読み取り時に返すschema。
    """

    id: int
    version: int

    model_config = {"from_attributes": True}


class RoleRead(BaseModel):
    """ロール読み取り時に返すschema。"""

    key: str
    name: str

    model_config = {"from_attributes": True}


class CurrentUserRead(UserRead):
    """現在のログインユーザー読み取り時に返すschema。"""

    system_roles: list[RoleRead]


class UserLogin(BaseModel):
    """
    ユーザーログインリクエストで受け取るschema。
    password は平文で受け取る。
    """

    email: EmailStr
    password: str


class UserLoginResponse(BaseModel):
    """
    ログイン成功時のレスポンスschema。
    """

    access_token: str | None = None
    token_type: str = "bearer"
