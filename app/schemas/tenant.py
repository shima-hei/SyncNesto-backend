"""組織と所属のAPI契約。Identityの認証情報は所属から変更しない。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

TenantRoleKey = Literal["tenant_owner", "tenant_admin", "tenant_member"]


class TenantRead(BaseModel):
    """組織メタデータ。"""

    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    status: str
    version: int


class TenantChoice(TenantRead):
    """本人が選択できる組織と組織Role。"""

    role_key: TenantRoleKey


class TenantCreate(BaseModel):
    """運営者が既存Identityを初期Ownerとして組織を作る。"""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(
        min_length=1, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )
    owner_email: EmailStr


class TenantUpdate(BaseModel):
    """組織内で変更する設定。"""

    model_config = ConfigDict(extra="forbid")
    version: int
    name: str = Field(min_length=1, max_length=255)


class TenantMemberRead(BaseModel):
    """現在の組織に属するユーザーの組織内情報。"""

    id: int
    user_id: int
    email: str
    display_name: str
    department: str | None
    position: str | None
    role_key: TenantRoleKey
    status: str
    version: int
    joined_at: datetime


class TenantMemberAdd(BaseModel):
    """メール完全一致で既存ユーザーを所属させる。"""

    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    role_key: TenantRoleKey = "tenant_member"


class TenantMemberUpdate(BaseModel):
    """組織内のRole・プロフィール・利用状態を変更する。"""

    model_config = ConfigDict(extra="forbid")
    version: int
    role_key: TenantRoleKey | None = None
    status: Literal["active", "suspended"] | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    department: str | None = Field(default=None, max_length=255)
    position: str | None = Field(default=None, max_length=255)


class TenantUserCreate(BaseModel):
    """新しいIdentityと現在の組織への所属を一緒に登録する。"""

    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=255)
    department: str | None = Field(default=None, max_length=255)
    position: str | None = Field(default=None, max_length=255)
    role_key: TenantRoleKey = "tenant_member"


class TenantUserCreated(BaseModel):
    """登録直後だけ表示する初期パスワード。再表示しない。"""

    member: TenantMemberRead
    initial_password: str
