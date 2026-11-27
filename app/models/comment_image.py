"""コメント用非公開画像と投稿前の予約。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.comments import db_comment

COMMENT_KINDS = ("task", "requirement", "specification", "test_design")
BODY_KINDS = (
    "body_task",
    "body_requirement",
    "body_specification",
    "body_test_design",
    "body_document",
)
IMAGE_KINDS_SQL = ", ".join(f"'{kind}'" for kind in (*COMMENT_KINDS, *BODY_KINDS))


class CommentImage(Base):
    """画像は下書き中か、いずれか一つのコメントに所属する。"""

    __tablename__ = "comment_images"
    __table_args__ = (
        CheckConstraint(
            "(CASE WHEN task_comment_id IS NULL THEN 0 ELSE 1 END) + "
            "(CASE WHEN requirement_comment_id IS NULL THEN 0 ELSE 1 END) + "
            "(CASE WHEN specification_comment_id IS NULL THEN 0 ELSE 1 END) + "
            "(CASE WHEN test_comment_id IS NULL THEN 0 ELSE 1 END) <= 1",
            name="ck_comment_images_one_owner",
        ),
        CheckConstraint(
            f"kind IN ({IMAGE_KINDS_SQL}) AND "
            "(task_comment_id IS NULL OR kind = 'task') AND "
            "(requirement_comment_id IS NULL OR kind = 'requirement') AND "
            "(specification_comment_id IS NULL OR kind = 'specification') AND "
            "(test_comment_id IS NULL OR kind = 'test_design')",
            name="ck_comment_images_owner_kind",
        ),
        {"comment": db_comment("コメント画像", "専用の非公開画像と投稿の所属")},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, comment="画像識別子")
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, comment="保有案件"
    )
    uploaded_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), comment="送信者"
    )
    kind: Mapped[str] = mapped_column(String(20), comment="コメント種別")
    filename: Mapped[str] = mapped_column(String(255), comment="画像の表示名")
    content_type: Mapped[str] = mapped_column(String(32), comment="検証済みの画像形式")
    byte_size: Mapped[int] = mapped_column(comment="ファイルサイズ")
    storage_key: Mapped[str] = mapped_column(
        String(512), unique=True, comment="非公開ストレージのキー"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="アップロード日時"
    )
    retained_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="本文で初めて確定した日時"
    )
    task_comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_comments.id", ondelete="CASCADE"),
        index=True,
        comment="タスクコメント",
    )
    requirement_comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirement_target_comments.id", ondelete="CASCADE"),
        index=True,
        comment="要件の対象コメント",
    )
    specification_comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("specification_comments.id", ondelete="CASCADE"),
        index=True,
        comment="画面・API設計コメント",
    )
    test_comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_design_comments.id", ondelete="CASCADE"),
        index=True,
        comment="テスト設計コメント",
    )


def comment_images(foreign_key):
    """一覧で添付メタデータを一括取得する。"""
    return relationship(
        CommentImage,
        foreign_keys=[foreign_key],
        lazy="selectin",
        order_by=(CommentImage.created_at, CommentImage.id),
        passive_deletes="all",
    )
