"""通知対象ユーザーと本文内の出現位置を分離して保存する。"""

from typing import ClassVar

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from app.db.base import Base
from app.models.comments import db_comment

COMMENT_FOREIGN_KEYS = {
    "requirement_comments": "requirement_comment_id",
    "requirement_target_comments": "requirement_target_comment_id",
    "task_comments": "task_comment_id",
    "test_design_comments": "test_design_comment_id",
}


class CommentMention(Base):
    """コメントとユーザーを一意に関連付け、複数の表示位置を保持する。"""

    __tablename__ = "comment_mentions"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(requirement_comment_id, requirement_target_comment_id, "
            "task_comment_id, test_design_comment_id) = 1",
            name="ck_comment_mentions_one_parent",
        ),
        *(
            UniqueConstraint(column, "user_id", name=f"uq_mentions_{column}_user")
            for column in COMMENT_FOREIGN_KEYS.values()
        ),
        {"comment": db_comment("コメントメンション", "一意な通知対象と本文内位置")},
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    requirement_comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirement_comments.id", ondelete="CASCADE")
    )
    requirement_target_comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirement_target_comments.id", ondelete="CASCADE")
    )
    task_comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_comments.id", ondelete="CASCADE")
    )
    test_design_comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_design_comments.id", ondelete="CASCADE")
    )
    occurrences: Mapped[list[dict]] = mapped_column(
        JSONB, comment=db_comment("出現位置", "UTF-16開始位置・終了位置・表示名")
    )


class MentionedComment:
    """既存コメントへ共通のメンション関連を追加する。"""

    __tablename__: ClassVar[str]

    @declared_attr
    def mention_targets(cls) -> Mapped[list[CommentMention]]:
        """コメントの種類に対応した外部キーを利用する。"""
        column = COMMENT_FOREIGN_KEYS[cls.__tablename__]
        return relationship(
            CommentMention,
            foreign_keys=f"CommentMention.{column}",
            cascade="all, delete-orphan",
            lazy="selectin",
        )

    @property
    def mentions(self) -> list[dict]:
        """API表示用に本文中の出現を位置順で返す。"""
        return sorted(
            [
                {"user_id": target.user_id, **occurrence}
                for target in self.mention_targets
                for occurrence in target.occurrences
            ],
            key=lambda occurrence: occurrence["start"],
        )
