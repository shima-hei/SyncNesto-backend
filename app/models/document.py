"""要件定義書とは独立したProject文書、本文の版、添付と関連。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class ProjectDocument(Base):
    """Project内で共有するMarkdown文書。"""

    __tablename__ = "project_documents"
    __table_args__ = {
        "comment": db_comment("プロジェクト文書", "本文と現在の版を保持する")
    }
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
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


class DocumentRevision(Base):
    """上書きしない本文スナップショット。"""

    __tablename__ = "document_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "number"),
        {"comment": db_comment("文書版", "作成と本文更新のスナップショット")},
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("project_documents.id"), index=True
    )
    number: Mapped[int]
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DocumentAttachment(Base):
    """非公開ストレージ内の確定済み添付。"""

    __tablename__ = "document_attachments"
    __table_args__ = {
        "comment": db_comment("文書添付", "署名付き取得のみを許可するファイル")
    }
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("project_documents.id"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    byte_size: Mapped[int]
    storage_key: Mapped[str] = mapped_column(String(1000), unique=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentLink(Base):
    """同一Projectの要件、タスクまたはテスト設計への参照。"""

    __tablename__ = "document_links"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(requirement_id, task_id, design_id) = 1",
            name="ck_document_link_one_target",
        ),
        UniqueConstraint("document_id", "requirement_id"),
        UniqueConstraint("document_id", "task_id"),
        UniqueConstraint("document_id", "design_id"),
        {"comment": db_comment("文書関連", "型ごとの外部キーで対象を参照する")},
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("project_documents.id"), index=True
    )
    requirement_id: Mapped[int | None] = mapped_column(ForeignKey("requirements.id"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    design_id: Mapped[int | None] = mapped_column(ForeignKey("test_designs.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
