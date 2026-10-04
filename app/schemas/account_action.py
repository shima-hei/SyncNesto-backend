"""メール本人確認のAPI入出力。トークンをレスポンスに返さない。"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.account_action import AccountActionPurpose


class PasswordResetRequest(BaseModel):
    """登録先メールへのパスワード再設定申請。"""

    email: EmailStr
    model_config = {"extra": "forbid"}


class AccountActionToken(BaseModel):
    """ログやURLクエリへ露出しない確認トークン本文。"""

    token: str = Field(min_length=32, max_length=128)
    model_config = {"extra": "forbid"}


class PasswordResetConfirm(AccountActionToken):
    """受信者本人が選んだ新しいパスワード。"""

    password: str = Field(min_length=12, max_length=128)


class EmailChangeRequest(BaseModel):
    """現在と新しいメール双方への確認を必要とする変更申請。"""

    new_email: EmailStr
    model_config = {"extra": "forbid"}


class AccountActionMessage(BaseModel):
    """確認フローの案内。アカウントの存在やトークンは含めない。"""

    message: str


class AccountActionInspection(BaseModel):
    """受信者が押すボタンと有効期限だけを返す。"""

    purpose: AccountActionPurpose
    expires_at: datetime
    new_email: str | None
