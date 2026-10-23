"""Project限定の招待、短期メール確認、追記の業務経緯。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class ProjectGuestInvitation(Base):
    """参加資格をまだ与えず、宛先と招待時の条件を保持する。"""

    __tablename__ = "project_guest_invitations"
    __table_args__ = {"comment": db_comment("ゲスト招待", "Project限定の受諾前の依頼")}
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    recipient_email: Mapped[str] = mapped_column(String(255))
    recipient_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    display_label: Mapped[str] = mapped_column(String(255))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    participation_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    version: Mapped[int] = mapped_column(default=1)
    token_generation: Mapped[int] = mapped_column(default=1)
    token_hash: Mapped[str] = mapped_column(String(64))
    delivery_state: Mapped[str] = mapped_column(String(20), default="sending")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    accepted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    participation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project_guest_participations.id")
    )
    request_id: Mapped[UUID]
    request_digest: Mapped[str] = mapped_column(String(64))


class ProjectGuestInvitationCheck(Base):
    """ログイン画面遷移中だけメールの確認を引き継ぐ。"""

    __tablename__ = "project_guest_invitation_checks"
    __table_args__ = {
        "comment": db_comment("招待メール確認", "生メールtokenと異なる短期確認のhash")
    }
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    invitation_id: Mapped[UUID] = mapped_column(
        ForeignKey("project_guest_invitations.id"), index=True
    )
    token_generation: Mapped[int]
    proof_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectGuestInvitationEvent(Base):
    """監査とは別に、実際の操作種別と結果を追記する。"""

    __tablename__ = "project_guest_invitation_events"
    __table_args__ = {
        "comment": db_comment("招待業務履歴", "操作結果と実操作者の追記記録")
    }
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    invitation_id: Mapped[UUID] = mapped_column(
        ForeignKey("project_guest_invitations.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_kind: Mapped[str] = mapped_column(String(30))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    operation_id: Mapped[UUID]
    request_digest: Mapped[str] = mapped_column(String(64))
    is_root: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int]
    state: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(1000))
    result: Mapped[dict] = mapped_column(JSONB)
