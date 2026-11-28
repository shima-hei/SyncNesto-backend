"""画面設計モックアップの範囲指摘を版に結び付ける。"""

import sqlalchemy as sa

from alembic import op

revision = "714f7bb23d31"
down_revision = "4ce61bd208af"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存のフィールド指摘はNULLのまま扱う。"""
    op.add_column("specification_comments", sa.Column("quote_start", sa.Integer()))


def downgrade() -> None:
    """指摘と選択本文は保持し、範囲の位置だけを戻す。"""
    op.drop_column("specification_comments", "quote_start")
