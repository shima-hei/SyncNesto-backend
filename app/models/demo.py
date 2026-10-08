"""一時組織の寿命、所有Identity、回収待ちファイルを管理する。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DemoSession(Base):
    """失効後も物理削除が完了するまで残す回収台帳。"""

    __tablename__ = "demo_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), unique=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), unique=True)
    session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sessions.id"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(30))
    cleanup_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    cleanup_attempts: Mapped[int] = mapped_column(Integer, default=0)
    cleanup_error: Mapped[str | None] = mapped_column(String(30))
    cleaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DemoOwnedUser(Base):
    """既存の共有Identityを削除対象に含めないための所有台帳。"""

    __tablename__ = "demo_owned_users"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    demo_id: Mapped[UUID] = mapped_column(ForeignKey("demo_sessions.id"), index=True)


class DemoUpload(Base):
    """発行済みPUT URLと容量予約を、業務登録失敗後も保持する。"""

    __tablename__ = "demo_uploads"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    demo_id: Mapped[UUID] = mapped_column(ForeignKey("demo_sessions.id"), index=True)
    key: Mapped[str] = mapped_column(String(1000), unique=True)
    byte_size: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DemoStartBudget(Base):
    """生IPを保存せず、時刻枠ごとの発行回数をDBで共有する。"""

    __tablename__ = "demo_start_budgets"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[int] = mapped_column(Integer, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
