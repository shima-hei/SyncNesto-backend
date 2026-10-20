"""外部判断の正本を、明示公開版と本人の許可世代へ固定する対応。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class FixedApprovalGuestBinding(Base):
    """状態を複製せず、公開する表現と本人別許可を不変に結ぶ。"""

    __tablename__ = "fixed_approval_guest_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "publication_id"],
            ["artifact_publications.project_id", "artifact_publications.id"],
        ),
        ForeignKeyConstraint(
            ["project_id", "participation_id"],
            [
                "project_guest_participations.project_id",
                "project_guest_participations.id",
            ],
        ),
        ForeignKeyConstraint(
            ["project_id", "grant_id"],
            [
                "artifact_publication_grants.project_id",
                "artifact_publication_grants.id",
            ],
        ),
        ForeignKeyConstraint(
            ["publication_id", "public_target_node_id"],
            [
                "artifact_publication_nodes.publication_id",
                "artifact_publication_nodes.id",
            ],
        ),
        {
            "comment": db_comment(
                "外部固定承認の対応", "判断正本と公開内容・本人許可の不変な接続"
            )
        },
    )
    approval_id: Mapped[UUID] = mapped_column(
        ForeignKey("fixed_approval_requests.id"), primary_key=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    publication_id: Mapped[UUID] = mapped_column(index=True)
    public_target_node_id: Mapped[UUID]
    participation_id: Mapped[UUID] = mapped_column(index=True)
    grant_id: Mapped[UUID] = mapped_column(index=True)
    projection_version: Mapped[str] = mapped_column(String(50))
    public_scope_digest: Mapped[str] = mapped_column(String(64))
    public_context: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
