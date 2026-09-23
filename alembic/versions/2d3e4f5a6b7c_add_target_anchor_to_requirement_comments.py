"""add target anchor to requirement comments

Revision ID: 2d3e4f5a6b7c
Revises: 1c2d3e4f5a6b
Create Date: 2026-07-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.models.comments import db_comment

revision: str = "2d3e4f5a6b7c"
down_revision: str | Sequence[str] | None = "1c2d3e4f5a6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """requirement_target_commentsに対象アンカーを追加する。"""
    op.add_column(
        "requirement_target_comments",
        sa.Column(
            "target_anchor",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=db_comment(
                "対象アンカー",
                "コメント対象内の段落・選択範囲・フィールドなどを示す識別子",
            ),
        ),
    )


def downgrade() -> None:
    """requirement_target_commentsから対象アンカーを削除する。"""
    op.drop_column("requirement_target_comments", "target_anchor")
