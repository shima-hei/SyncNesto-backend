"""組織共通Identityの本人確認要求。平文の確認トークンは保存しない。"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class AccountActionPurpose(StrEnum):
    """受信者の明示操作が必要な確認ステップ。"""

    PASSWORD_RESET = "password_reset"
    EMAIL_CHANGE_APPROVE = "email_change_approve"
    EMAIL_CHANGE_VERIFY = "email_change_verify"


class AccountAction(Base):
    """一度だけ使用できる本人確認要求と送信受付結果。"""

    __tablename__ = "account_actions"
    __table_args__ = {
        "comment": db_comment("本人確認要求", "共通Identityのメール承認・再設定"),
    }

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"))
    purpose: Mapped[str] = mapped_column(String(50))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    credential_fingerprint: Mapped[str] = mapped_column(String(64))
    old_email: Mapped[str] = mapped_column(String(255))
    new_email: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
