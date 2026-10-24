"""固定要件の判断依頼と、取消・撤回を含む追記履歴。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class VerificationRequest(Base):
    """固定basisと業務版は、期限付きの確認作業版と分ける。"""

    __tablename__ = "requirement_verification_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_id", "plan_version"],
            [
                "requirement_verification_plan_revisions.plan_id",
                "requirement_verification_plan_revisions.version",
            ],
        ),
        UniqueConstraint(
            "project_id",
            "requested_by",
            "request_id",
            name="uq_verdict_request_operation",
        ),
        UniqueConstraint("previous_request_id", name="uq_verdict_successor"),
        Index(
            "uq_verdict_pending",
            "project_id",
            "source_requirement_id",
            unique=True,
            postgresql_where=text("status='requested'"),
        ),
        Index(
            "ix_verdict_source",
            "project_id",
            "source_requirement_id",
            "created_at",
            "id",
        ),
        {"comment": db_comment("要件の判断依頼", "固定した確認対象と指定本人の判断")},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    source_requirement_id: Mapped[int]
    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("requirement_verification_plans.id")
    )
    plan_version: Mapped[int]
    subject_digest: Mapped[str] = mapped_column(String(64))
    checklist_digest: Mapped[str] = mapped_column(String(64))
    basis: Mapped[dict] = mapped_column(JSONB)
    basis_digest: Mapped[str] = mapped_column(String(64))
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    requester_name: Mapped[str] = mapped_column(String(200))
    judge_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    judge_name: Mapped[str] = mapped_column(String(200))
    previous_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("requirement_verification_requests.id")
    )
    reviewed_decision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("requirement_verification_requests.id")
    )
    reason: Mapped[str]
    request_id: Mapped[UUID]
    request_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    status: Mapped[str] = mapped_column(String(20), default="requested")
    version: Mapped[int] = mapped_column(default=1)
    result: Mapped[str | None] = mapped_column(String(20))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decision_reason: Mapped[str | None]
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    review: Mapped[dict | None] = mapped_column(JSONB)
    review_version: Mapped[int] = mapped_column(default=0)
    lease_id: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VerificationEvent(Base):
    """元の判断を変更しない業務履歴。監査ログとは別に保持する。"""

    __tablename__ = "requirement_verification_events"
    __table_args__ = (
        UniqueConstraint("request_id", "version", name="uq_verdict_event_version"),
        UniqueConstraint(
            "request_id", "operation_id", name="uq_verdict_event_operation"
        ),
        {"comment": db_comment("要件の判断履歴", "依頼・判断・取消・撤回の追記記録")},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("requirement_verification_requests.id")
    )
    version: Mapped[int]
    event: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    actor_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    reason: Mapped[str]
    operation_id: Mapped[UUID]
    operation_digest: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSONB)
