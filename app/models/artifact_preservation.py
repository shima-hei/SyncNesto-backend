"""固定本文に結び付ける添付構成と、書込み前から残す保全台帳。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
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


class ArtifactPreservation(Base):
    """元データを変更せず、準備完了時に添付manifestを封印する。"""

    __tablename__ = "artifact_preservations"
    __table_args__ = (
        UniqueConstraint(
            "release_id",
            "created_by",
            "request_id",
            name="uq_artifact_preservation_request",
        ),
        CheckConstraint(
            "status IN ('preparing','ready','failed','cancelled')",
            name="ck_artifact_preservation_status",
        ),
        CheckConstraint(
            "jsonb_array_length(selection) BETWEEN 1 AND 20",
            name="ck_artifact_preservation_selection",
        ),
        {
            "comment": db_comment(
                "添付保全構成", "固定本文と選択した添付実体の対応と準備状態"
            )
        },
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    release_id: Mapped[UUID] = mapped_column(
        ForeignKey("artifact_releases.id"), index=True
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    request_id: Mapped[UUID]
    request_digest: Mapped[str] = mapped_column(String(64))
    plan_digest: Mapped[str] = mapped_column(String(64))
    selection: Mapped[list] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="preparing")
    schema_version: Mapped[int] = mapped_column(default=1)
    manifest: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    manifest_digest: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(50))
    lease_id: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ArtifactPreservationObject(Base):
    """試行ごとに異なる保全キーを、PUTより先に記録する。"""

    __tablename__ = "artifact_preservation_objects"
    __table_args__ = (
        Index(
            "uq_artifact_preservation_selected",
            "preservation_id",
            "slot",
            unique=True,
            postgresql_where=text("selected"),
        ),
        CheckConstraint("slot BETWEEN 0 AND 19", name="ck_artifact_preservation_slot"),
        CheckConstraint(
            "state IN ('reserved','verified','abandoned')",
            name="ck_artifact_preservation_object_state",
        ),
        CheckConstraint(
            "NOT selected OR (state='verified' "
            "AND content_digest IS NOT NULL AND byte_size > 0)",
            name="ck_artifact_preservation_verified",
        ),
        {
            "comment": db_comment(
                "添付保全オブジェクト台帳",
                "書込み予約・検証・採用と非採用実体の回収履歴",
            )
        },
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    preservation_id: Mapped[UUID] = mapped_column(
        ForeignKey("artifact_preservations.id"), index=True
    )
    slot: Mapped[int]
    lease_id: Mapped[UUID]
    storage_key: Mapped[str] = mapped_column(String(1000), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    state: Mapped[str] = mapped_column(String(20), default="reserved")
    selected: Mapped[bool] = mapped_column(default=False)
    content_digest: Mapped[str | None] = mapped_column(String(64))
    byte_size: Mapped[int | None]
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_error: Mapped[bool] = mapped_column(default=False)
