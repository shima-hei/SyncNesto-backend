"""add comment mentions

Revision ID: 73c08ecae547
Revises: 4d5e6f708192
Create Date: 2026-09-29 00:08:26.337770

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "73c08ecae547"
down_revision: Union[str, Sequence[str], None] = "4d5e6f708192"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    columns = {
        "requirement_comment_id": "requirement_comments",
        "requirement_target_comment_id": "requirement_target_comments",
        "task_comment_id": "task_comments",
        "test_design_comment_id": "test_design_comments",
    }
    op.create_table(
        "comment_mentions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        *(
            sa.Column(
                column, sa.Integer(), sa.ForeignKey(f"{table}.id", ondelete="CASCADE")
            )
            for column, table in columns.items()
        ),
        sa.Column(
            "occurrences",
            postgresql.JSONB(),
            nullable=False,
            comment="出現位置: UTF-16開始位置・終了位置・表示名",
        ),
        sa.CheckConstraint(
            "num_nonnulls(requirement_comment_id, requirement_target_comment_id, "
            "task_comment_id, test_design_comment_id) = 1",
            name="ck_comment_mentions_one_parent",
        ),
        *(
            sa.UniqueConstraint(column, "user_id", name=f"uq_mentions_{column}_user")
            for column in columns
        ),
        comment="コメントメンション: 一意な通知対象と本文内位置",
    )
    op.create_index("ix_comment_mentions_user_id", "comment_mentions", ["user_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("comment_mentions")
