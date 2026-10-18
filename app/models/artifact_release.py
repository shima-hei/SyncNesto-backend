"""元の業務データから独立して保持する、封印済み固定版。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class ArtifactRelease(Base):
    """本文hashと作成時点を持つ、編集不可の固定版ヘッダー。"""

    __tablename__ = "artifact_releases"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "created_by", "request_id", name="uq_artifact_release_request"
        ),
        CheckConstraint(
            "source_kind IN "
            "('requirement','requirement_document','specification','test_design')",
            name="ck_artifact_release_source_kind",
        ),
        Index("ix_artifact_release_source", "project_id", "source_kind", "source_id"),
        {"comment": db_comment("成果物固定版", "本文と構成の不変な保存単位")},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    source_kind: Mapped[str] = mapped_column(String(30))
    source_id: Mapped[int]
    source_version: Mapped[int]
    source_refs: Mapped[list] = mapped_column(JSONB)
    source_kinds: Mapped[list] = mapped_column(JSONB)
    schema_version: Mapped[int] = mapped_column(default=1)
    projection_version: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(255))
    content_digest: Mapped[str] = mapped_column(String(64))
    node_count: Mapped[int]
    byte_size: Mapped[int]
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[UUID]
    request_digest: Mapped[str] = mapped_column(String(64))
    sealed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class ArtifactReleaseNode(Base):
    """固定版内だけで親子関係を持つ本文。元対象のFKは持たない。"""

    __tablename__ = "artifact_release_nodes"
    __table_args__ = (
        UniqueConstraint("release_id", "id", name="uq_artifact_release_node_identity"),
        UniqueConstraint(
            "release_id", "ordinal", name="uq_artifact_release_node_ordinal"
        ),
        ForeignKeyConstraint(
            ["release_id", "parent_id"],
            ["artifact_release_nodes.release_id", "artifact_release_nodes.id"],
            name="fk_artifact_release_node_parent",
        ),
        CheckConstraint("id <> parent_id", name="ck_artifact_release_node_not_self"),
        {"comment": db_comment("固定版本文", "同じ固定版に属する本文と表示順")},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    release_id: Mapped[UUID] = mapped_column(ForeignKey("artifact_releases.id"))
    parent_id: Mapped[UUID | None]
    ordinal: Mapped[int]
    position: Mapped[int]
    node_kind: Mapped[str] = mapped_column(String(30))
    source_id: Mapped[int]
    source_version: Mapped[int | None]
    payload: Mapped[dict] = mapped_column(JSONB)
    content_digest: Mapped[str] = mapped_column(String(64))
