"""テストケースが最後に確認した設計内容を保持する。"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "2b3c4d5e6f70"
down_revision = "1a2b3c4d5e6f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "test_cases", sa.Column("acknowledged_source", JSONB(), nullable=True)
    )


def downgrade():
    op.drop_column("test_cases", "acknowledged_source")
