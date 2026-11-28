"""案件内で依頼した複数設計の対象版と判断。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DesignReviewRequest(Base):
    """担当者への依頼を1件にまとめ、結論と確認時点を保存する。"""

    __tablename__ = "design_review_requests"
    __table_args__ = (
        CheckConstraint("kind IN ('review', 'approval')", name="ck_design_review_kind"),
        CheckConstraint(
            "status IN ('pending', 'reviewed', 'approved', "
            "'changes_requested', 'cancelled')",
            name="ck_design_review_status",
        ),
        Index("ix_design_review_reviewer", "project_id", "reviewer_id", "status"),
        Index("ix_design_review_requester", "project_id", "requester_id", "status"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(24))
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    request_digest: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(Text)
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DesignReviewRequestTarget(Base):
    """現在名や状態の更新を受けず、依頼時の設計と版を識別する。"""

    __tablename__ = "design_review_request_targets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "request_id"],
            ["design_review_requests.project_id", "design_review_requests.id"],
            name="fk_design_review_target_request",
            ondelete="CASCADE",
        ),
        CheckConstraint("version >= 1", name="ck_design_review_target_version"),
        Index(
            "ix_design_review_target_specification", "project_id", "specification_id"
        ),
    )

    project_id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[UUID] = mapped_column(primary_key=True)
    specification_id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int]
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
