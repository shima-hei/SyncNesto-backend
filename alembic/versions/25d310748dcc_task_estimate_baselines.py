"""Persist explicitly confirmed task estimate baselines."""

import sqlalchemy as sa

from alembic import op

revision = "25d310748dcc"
down_revision = "1e0a845c297d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_estimate_baselines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "estimate_revision_id",
            sa.Integer(),
            sa.ForeignKey("task_estimate_revisions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "confirmed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("reason", sa.String(500)),
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'syncnesto_app') THEN
            GRANT SELECT, INSERT ON task_estimate_baselines TO syncnesto_app;
            GRANT USAGE, SELECT ON task_estimate_baselines_id_seq TO syncnesto_app;
        END IF;
    END
    $$;
    """)


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM task_estimate_baselines)")
    ):
        raise RuntimeError("Task estimate baselines are in use")
    op.drop_table("task_estimate_baselines")
