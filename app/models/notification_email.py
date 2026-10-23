"""本人設定と、業務判断から分離したメール配送の記録。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class NotificationEmailPreference(Base):
    """全参加案件共通の本人による通知先の選択。"""

    __tablename__ = "notification_email_preferences"
    __table_args__ = (
        CheckConstraint(
            "version>0 AND (NOT enabled OR email_digest IS NOT NULL)",
            name="ck_email_preference",
        ),
        {"comment": db_comment("メール通知設定", "本人の選択。初期OFF")},
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    email_digest: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class NotificationEmailDelivery(Base):
    """一つの新規通知について一度だけ作る配送台帳。"""

    __tablename__ = "notification_email_deliveries"
    __table_args__ = (
        UniqueConstraint("notification_id", name="uq_email_delivery_notification"),
        CheckConstraint(
            "state IN ('pending','leased','sending','retry_wait','accepted',"
            "'unknown','failed','suppressed') AND version>0 "
            "AND attempt_count>=0 AND preference_version>0",
            name="ck_email_delivery_state",
        ),
        Index(
            "ix_email_delivery_due",
            "next_attempt_at",
            "id",
            postgresql_where=text("state IN ('pending','retry_wait')"),
        ),
        {"comment": db_comment("通知メール配送", "業務状態とは独立した送信待ち")},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    notification_id: Mapped[int] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE")
    )
    preference_version: Mapped[int]
    email_digest: Mapped[str] = mapped_column(String(64))
    template_version: Mapped[int] = mapped_column(default=1, server_default="1")
    content_digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(
        String(24), default="pending", server_default="pending"
    )
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    attempt_count: Mapped[int] = mapped_column(default=0, server_default="0")
    claim_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class NotificationEmailAttempt(Base):
    """開始・結果・運用者の確認を上書きせず別行へ追記する。"""

    __tablename__ = "notification_email_attempts"
    __table_args__ = (
        UniqueConstraint("attempt_id", "record_kind", name="uq_email_attempt_record"),
        CheckConstraint(
            "record_kind IN ('started','completed','resolution') "
            "AND operator_kind IN ('worker','local_operator')",
            name="ck_email_attempt_record",
        ),
        {
            "comment": db_comment(
                "通知メール送信試行", "安全な結果codeのみ。本文や宛先を記録しない"
            )
        },
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    delivery_id: Mapped[UUID] = mapped_column(
        ForeignKey("notification_email_deliveries.id", ondelete="CASCADE"), index=True
    )
    attempt_id: Mapped[UUID]
    record_kind: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str | None] = mapped_column(String(24))
    safe_code: Mapped[str | None] = mapped_column(String(64))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    operator_kind: Mapped[str] = mapped_column(String(24), default="worker")
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
