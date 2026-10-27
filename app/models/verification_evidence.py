"""選んだ確認表版に提出した証拠と、保全コピーの試行台帳。"""

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


class VerificationEvidenceSet(Base):
    """入力と対象は不変、readyは保全完了であって合格ではない。"""

    __tablename__ = "requirement_verification_evidence_sets"
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
            "created_by",
            "plan_id",
            "request_id",
            name="uq_verification_evidence_request",
        ),
        Index("ix_verification_evidence_page", "plan_id", "created_at", "id"),
        {"comment": db_comment("検証証拠セット", "固定した確認表版への提出と保全状態")},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("requirement_verification_plans.id")
    )
    plan_version: Mapped[int]
    schema_version: Mapped[int] = mapped_column(default=1)
    subject_digest: Mapped[str] = mapped_column(String(64))
    checklist_digest: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    actor_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    request_id: Mapped[UUID]
    request_digest: Mapped[str] = mapped_column(String(64))
    preview_digest: Mapped[str] = mapped_column(String(64))
    selection: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="preparing")
    version: Mapped[int] = mapped_column(default=1)
    manifest: Mapped[dict | None] = mapped_column(JSONB)
    manifest_digest: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_id: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    safe_error: Mapped[str | None] = mapped_column(String(50))


class VerificationEvidenceObject(Base):
    """PUTより先にcommitする予約。元ファイルへの削除FKは設けない。"""

    __tablename__ = "requirement_verification_evidence_objects"
    __table_args__ = (
        UniqueConstraint("storage_key"),
        Index(
            "uq_verification_evidence_selected",
            "set_id",
            "slot",
            unique=True,
            postgresql_where=text("selected"),
        ),
        {
            "comment": db_comment(
                "検証証拠の保全試行", "VersionIdと保全時bytesの同一性を記録する"
            )
        },
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    set_id: Mapped[UUID] = mapped_column(
        ForeignKey("requirement_verification_evidence_sets.id"), index=True
    )
    slot: Mapped[int]
    attempt_id: Mapped[UUID]
    storage_key: Mapped[str] = mapped_column(String(200))
    destination_version: Mapped[str | None] = mapped_column(String(300))
    state: Mapped[str] = mapped_column(String(20), default="reserved")
    selected: Mapped[bool] = mapped_column(default=False)
    byte_size: Mapped[int | None]
    sha256: Mapped[str | None] = mapped_column(String(64))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_error: Mapped[str | None] = mapped_column(String(50))
