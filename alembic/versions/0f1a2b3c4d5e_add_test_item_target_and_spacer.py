"""テスト項目の対象画面・機能と明示的な区切り行。"""

import sqlalchemy as sa

from alembic import op

revision = "0f1a2b3c4d5e"
down_revision = "9e0f1a2b3c4d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "test_items",
        sa.Column("target_feature", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "test_items",
        sa.Column("is_spacer", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("test_items", "is_spacer")
    op.drop_column("test_items", "target_feature")
