"""
ユーザーに関するPydanticスキーマを定義するモジュール。

リクエスト（作成）およびレスポンス（読み取り）で使用するデータ構造を定義する。
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


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
    department: str | None = None
    position: str | None = None
    is_active: bool = True
    system_role_keys: list[str] = Field(default_factory=list)


class UserUpdate(BaseModel):
    """ユーザー更新リクエストで受け取るschema。"""

    version: int
    email: EmailStr | None = None
    name: str | None = None
    password: str | None = None
    department: str | None = None
    position: str | None = None
    is_active: bool | None = None
    system_role_keys: list[str] | None = None


class UserProfileUpdate(BaseModel):
    """本人プロフィール更新リクエストで受け取るschema。"""

    version: int
    name: str | None = None
    password: str | None = None

    model_config = {"extra": "forbid"}


class RoleRead(BaseModel):
    """ロール読み取り時に返すschema。"""

    key: str
    name: str

    model_config = {"from_attributes": True}


class UserRead(UserBase):
    """ユーザー読み取り時に返すschema。"""

    id: int
    version: int
    department: str | None = None
    position: str | None = None
    avatar_url: str | None = None
    is_active: bool
    last_login_at: datetime | None = None
    created_by: int | None = None
    updated_by: int | None = None
    system_roles: list[RoleRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class UserListItem(UserBase):
    """ユーザー一覧で返す軽量schema。"""

    id: int
    department: str | None = None
    position: str | None = None
    avatar_url: str | None = None
    is_active: bool
    last_login_at: datetime | None = None
    system_roles: list[RoleRead] = Field(default_factory=list)


class UserListResponse(BaseModel):
    """ユーザー一覧レスポンスschema。"""

    items: list[UserListItem]
    total: int
    page: int
    page_size: int


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
