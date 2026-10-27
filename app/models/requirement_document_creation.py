"""標準章付き文書の作成結果を同じ送信へ結び付ける。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class RequirementDocumentCreationReceipt(Base):
    """文書削除後も同じ送信による再作成を防ぐ記録。"""

    __tablename__ = "requirement_document_creation_receipts"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "actor_id",
            "request_key",
            name="uq_requirement_document_creation_request",
        ),
        {"comment": db_comment("標準章付き作成結果", "本人とProjectごとの再送結果")},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    request_key: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    request_hash: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirement_documents.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
