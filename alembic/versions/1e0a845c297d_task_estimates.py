"""Store task estimate revisions without backfilling task values."""

import sqlalchemy as sa

from alembic import op

revision = "1e0a845c297d"
down_revision = "0a27d9c7859b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_estimate_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_version", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(16)),
        sa.Column("minutes", sa.Integer()),
        sa.Column("optimistic_minutes", sa.Integer()),
        sa.Column("likely_minutes", sa.Integer()),
        sa.Column("pessimistic_minutes", sa.Integer()),
        sa.Column("assumptions", sa.String(2000)),
        sa.Column("change_reason", sa.String(500)),
        sa.Column(
            "actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("task_id", "task_version"),
    )
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'syncnesto_app') THEN
            GRANT SELECT, INSERT ON task_estimate_revisions TO syncnesto_app;
            GRANT USAGE, SELECT ON task_estimate_revisions_id_seq TO syncnesto_app;
        END IF;
    END
    $$;
    """)


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM task_estimate_revisions)")
    ):
        raise RuntimeError("Task estimate revisions are in use")
    op.drop_table("task_estimate_revisions")
