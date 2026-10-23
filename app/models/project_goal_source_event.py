"""案件の目標出典の選択を、本文や承認と分けて記録する。"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class ProjectGoalSourceEvent(Base):
    """参照専用版ごとの追記専用の業務履歴。"""

    __tablename__ = "project_goal_source_events"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_goal_source_event_version"),
        ForeignKeyConstraint(
            ["project_id", "previous_document_id"],
            ["requirement_documents.project_id", "requirement_documents.id"],
            name="fk_goal_event_previous",
        ),
        ForeignKeyConstraint(
            ["project_id", "document_id"],
            ["requirement_documents.project_id", "requirement_documents.id"],
            name="fk_goal_event_document",
        ),
        CheckConstraint(
            "previous_document_id IS DISTINCT FROM document_id",
            name="ck_goal_event_changed",
        ),
        CheckConstraint(
            "previous_version > 0 AND version = previous_version + 1",
            name="ck_goal_event_version",
        ),
        CheckConstraint(
            "(document_id IS NULL AND document_version IS NULL) OR "
            "(document_id IS NOT NULL AND document_version IS NOT NULL "
            "AND document_version > 0)",
            name="ck_goal_event_document_version",
        ),
        CheckConstraint("char_length(reason) <= 2000", name="ck_goal_event_reason"),
        Index("ix_goal_source_event_page", "project_id", "id"),
        {
            "comment": db_comment(
                "目標出典選択履歴", "本文・承認とは別の案件共通参照の変更履歴"
            )
        },
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    previous_document_id: Mapped[int | None]
    document_id: Mapped[int | None]
    previous_version: Mapped[int]
    version: Mapped[int]
    document_version: Mapped[int | None]
    reason: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
