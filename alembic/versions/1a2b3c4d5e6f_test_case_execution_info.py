"""テストケースに最終実行者と実行日時を追加する。"""

import sqlalchemy as sa

from alembic import op

revision = "1a2b3c4d5e6f"
down_revision = "0f1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("test_cases", sa.Column("executed_by", sa.Integer(), nullable=True))
    op.add_column(
        "test_cases",
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_test_cases_executed_by_users",
        "test_cases",
        "users",
        ["executed_by"],
        ["id"],
    )


def downgrade():
    op.drop_constraint(
        "fk_test_cases_executed_by_users", "test_cases", type_="foreignkey"
    )
    op.drop_column("test_cases", "executed_at")
    op.drop_column("test_cases", "executed_by")
