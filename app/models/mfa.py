"""Identity共通の追加認証・一度限りの復旧を保存する。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class MfaFactor(Base):
    """検証済みの認証器。TOTP秘密だけは専用鍵で暗号化する。"""

    __tablename__ = "mfa_factors"
    __table_args__ = {"comment": db_comment("MFA認証器", "本人のTOTPまたはPasskey")}

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    secret: Mapped[str | None] = mapped_column(Text)
    credential_id: Mapped[str | None] = mapped_column(Text, unique=True)
    public_key: Mapped[str | None] = mapped_column(Text)
    sign_count: Mapped[int] = mapped_column(BigInteger, default=0)
    last_step: Mapped[int] = mapped_column(BigInteger, default=-1)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MfaFlow(Base):
    """パスワード検証後の限定Cookieと認証器challengeを結ぶ。"""

    __tablename__ = "mfa_flows"
    __table_args__ = {
        "comment": db_comment("MFA限定状態", "目的と本人に固定した短期要求")
    }

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))
    security_version: Mapped[int] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)


class MfaRecoveryCode(Base):
    """一度だけ使える高エントロピーの復旧コードのハッシュ。"""

    __tablename__ = "mfa_recovery_codes"
    __table_args__ = {
        "comment": db_comment("MFA復旧コード", "平文を再表示しない復旧手段")
    }

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MfaRecoveryRequest(Base):
    """運営者による本人確認と、未使用の再登録要求。"""

    __tablename__ = "mfa_recovery_requests"
    __table_args__ = {
        "comment": db_comment("MFA復旧申請", "本人確認後の再登録を監査する")
    }

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    verification_method: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(String(500))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    security_version: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
