"""Persist the project Gantt task order independently of the board."""

import sqlalchemy as sa

from alembic import op

revision = "ba34f18c29d0"
down_revision = "c71a8d9320ef"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_gantt_orders",
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False),
    )
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'syncnesto_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON task_gantt_orders TO syncnesto_app;
        END IF;
    END
    $$;
    """)


def downgrade() -> None:
    op.drop_table("task_gantt_orders")
