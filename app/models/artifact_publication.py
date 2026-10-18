"""閲覧許可とは独立した、編集不可の公開準備版。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class ArtifactPublication(Base):
    """公開する表現と内部固定版の対応を封印する。公開許可は持たない。"""

    __tablename__ = "artifact_publications"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "created_by",
            "request_id",
            name="uq_artifact_publication_request",
        ),
        CheckConstraint(
            "schema_version = 1 AND projection_version = 'public-preview-v1'",
            name="ck_artifact_publication_schema",
        ),
        CheckConstraint(
            "node_count BETWEEN 1 AND 50000 AND byte_size BETWEEN 1 AND 33554432",
            name="ck_artifact_publication_capacity",
        ),
        {"comment": db_comment("公開準備版", "内部固定版から作る不変な未公開の表現")},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    release_id: Mapped[UUID] = mapped_column(
        ForeignKey("artifact_releases.id"), index=True
    )
    preservation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("artifact_preservations.id")
    )
    schema_version: Mapped[int] = mapped_column(default=1)
    projection_version: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(255))
    source_kinds: Mapped[list] = mapped_column(JSONB)
    source_manifest: Mapped[dict] = mapped_column(JSONB)
    source_manifest_digest: Mapped[str] = mapped_column(String(64))
    file_manifest: Mapped[list] = mapped_column(JSONB)
    public_digest: Mapped[str] = mapped_column(String(64))
    body_digest: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int]
    node_count: Mapped[int]
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[UUID]
    request_digest: Mapped[str] = mapped_column(String(64))
    sealed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class ArtifactPublicationNode(Base):
    """公開版内の親子構造と公開用payload。内部source IDを持たない。"""

    __tablename__ = "artifact_publication_nodes"
    __table_args__ = (
        UniqueConstraint(
            "publication_id", "id", name="uq_artifact_publication_node_identity"
        ),
        UniqueConstraint(
            "publication_id", "ordinal", name="uq_artifact_publication_node_ordinal"
        ),
        ForeignKeyConstraint(
            ["publication_id", "parent_id"],
            [
                "artifact_publication_nodes.publication_id",
                "artifact_publication_nodes.id",
            ],
            name="fk_artifact_publication_node_parent",
        ),
        CheckConstraint(
            "id <> parent_id AND ordinal >= 0 AND position >= 0",
            name="ck_artifact_publication_node_order",
        ),
        CheckConstraint(
            "node_kind IN ('requirement_document','section','requirement',"
            "'requirement_detail','specification','test_design','test_design_part')",
            name="ck_artifact_publication_node_kind",
        ),
        {"comment": db_comment("公開準備版本文", "公開用表現の本文と順序")},
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    publication_id: Mapped[UUID] = mapped_column(
        ForeignKey("artifact_publications.id"), index=True
    )
    parent_id: Mapped[UUID | None]
    ordinal: Mapped[int]
    position: Mapped[int]
    node_kind: Mapped[str] = mapped_column(String(30))
    source_version: Mapped[int | None]
    payload: Mapped[dict] = mapped_column(JSONB)
    content_digest: Mapped[str] = mapped_column(String(64))
