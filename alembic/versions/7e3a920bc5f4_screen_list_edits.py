"""画面一覧の担当と一括保存の再送結果を保持する。"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "7e3a920bc5f4"
down_revision = "18a73641dbe5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("specifications", sa.Column("assignee_id", sa.Integer()))
    op.create_foreign_key(
        "fk_specifications_assignee", "specifications", "users", ["assignee_id"], ["id"]
    )
    op.create_index("ix_specifications_assignee_id", "specifications", ["assignee_id"])
    op.create_table(
        "screen_list_edit_receipts",
        sa.Column("project_id", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), primary_key=True),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("result", JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
    )


def downgrade() -> None:
    op.drop_table("screen_list_edit_receipts")
    op.drop_index("ix_specifications_assignee_id", table_name="specifications")
    op.drop_constraint(
        "fk_specifications_assignee", "specifications", type_="foreignkey"
    )
    op.drop_column("specifications", "assignee_id")
