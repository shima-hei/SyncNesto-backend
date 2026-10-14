"""Projectで共有するAPI・画面仕様と、版・関連・箇所指定レビュー。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class Specification(Base):
    """項目のUUIDを維持する構造化した設計書。"""

    __tablename__ = "specifications"
    __table_args__ = {
        "comment": db_comment("設計書", "Projectで共有するAPI・画面の仕様")
    }
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="draft")
    content: Mapped[dict] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SpecificationRevision(Base):
    """本文・状態の更新時に保持するスナップショット。"""

    __tablename__ = "specification_revisions"
    __table_args__ = (
        UniqueConstraint("specification_id", "number"),
        {"comment": db_comment("設計書版", "本文・状態のスナップショット")},
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    specification_id: Mapped[int] = mapped_column(
        ForeignKey("specifications.id"), index=True
    )
    number: Mapped[int]
    snapshot: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SpecificationComment(Base):
    """表示行番号ではなく、フィールドと項目UUIDへ指摘を紐づける。"""

    __tablename__ = "specification_comments"
    __table_args__ = {
        "comment": db_comment("設計書指摘", "項目UUID・版を指定するレビュー指摘")
    }
    id: Mapped[int] = mapped_column(primary_key=True)
    specification_id: Mapped[int] = mapped_column(
        ForeignKey("specifications.id"), index=True
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    field_path: Mapped[str] = mapped_column(String(300))
    field_snapshot: Mapped[str] = mapped_column(Text)
    field_digest: Mapped[str] = mapped_column(String(64))
    specification_version: Mapped[int]
    body: Mapped[str] = mapped_column(Text)
    is_resolved: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SpecificationLink(Base):
    """同一Project内で、権限を確認した資源のみを関連付ける。"""

    __tablename__ = "specification_links"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(requirement_id, task_id, test_item_id, "
            "related_specification_id, document_id) = 1",
            name="ck_specification_link_one_target",
        ),
        UniqueConstraint("specification_id", "requirement_id"),
        UniqueConstraint("specification_id", "task_id"),
        UniqueConstraint("specification_id", "test_item_id"),
        UniqueConstraint("specification_id", "related_specification_id"),
        UniqueConstraint("specification_id", "document_id"),
        {
            "comment": db_comment(
                "設計書関連",
                "同一Project内の設計・要件・タスク・テスト・参考文書の関連",
            )
        },
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    specification_id: Mapped[int] = mapped_column(
        ForeignKey("specifications.id"), index=True
    )
    requirement_id: Mapped[int | None] = mapped_column(ForeignKey("requirements.id"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    test_item_id: Mapped[UUID | None] = mapped_column(ForeignKey("test_items.id"))
    related_specification_id: Mapped[int | None] = mapped_column(
        ForeignKey("specifications.id")
    )
    document_id: Mapped[int | None] = mapped_column(ForeignKey("project_documents.id"))
