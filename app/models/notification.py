"""業務状態とは独立した、ユーザー宛ての過去イベント。"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class NotificationType(StrEnum):
    """表示テンプレートと通知生成ポリシーを識別する。"""

    ASSIGNED = "assigned"
    MENTIONED = "mentioned"


class NotificationTargetType(StrEnum):
    """URLではなく、業務リソースの安定した種類を保存する。"""

    TASK = "task"
    REQUIREMENT = "requirement"
    OPEN_ISSUE = "open_issue"
    TASK_COMMENT = "task_comment"
    REQUIREMENT_COMMENT = "requirement_comment"
    REQUIREMENT_TARGET_COMMENT = "requirement_target_comment"
    TEST_DESIGN_COMMENT = "test_design_comment"


class Notification(Base):
    """宛先、対象、表示用スナップショット、既読日時を保持する。"""

    __tablename__ = "notifications"
    __table_args__ = (
        Index(
            "ix_notifications_tenant_recipient_created",
            "tenant_id",
            "recipient_user_id",
            "created_at",
            "id",
        ),
        UniqueConstraint(
            "event_key", "recipient_user_id", name="uq_notification_event_recipient"
        ),
        Index(
            "ix_notifications_recipient_created",
            "recipient_user_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_notifications_recipient_read_created",
            "recipient_user_id",
            "read_at",
            "created_at",
            "id",
        ),
        Index(
            "ix_notifications_recipient_project_created",
            "recipient_user_id",
            "project_id",
            "created_at",
            "id",
        ),
        {
            "comment": db_comment(
                "ユーザー通知", "確認する価値のあるユーザー宛てイベント"
            )
        },
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    recipient_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(String(64))
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(64))
    event_key: Mapped[str] = mapped_column(
        String(200),
        comment=db_comment("イベントキー", "対象種別・ID・バージョン・通知種別"),
    )
    snapshot: Mapped[dict] = mapped_column(
        JSONB,
        comment=db_comment("表示スナップショット", "操作時の名前・名称・コメント抜粋"),
    )
    context: Mapped[dict] = mapped_column(
        JSONB,
        comment=db_comment("遷移コンテキスト", "要件定義書等の親ID。URLは保存しない"),
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
