"""Retain whether a publication grant was issued with a guest decision request."""

import sqlalchemy as sa
from alembic import op

revision = "90ce274d18fa"
down_revision = "5fa16d2c870b"


def upgrade() -> None:
    op.add_column(
        "artifact_publication_grants",
        sa.Column(
            "decision_only", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_index(
        "ix_requirement_workflow_guest_grant",
        "requirement_workflow_guest_bindings",
        ["grant_id"],
    )


def downgrade() -> None:
    op.execute("LOCK TABLE artifact_publication_grants IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT FROM artifact_publication_grants WHERE decision_only) THEN
            RAISE EXCEPTION 'decision grant provenance must be retained';
          END IF;
        END $$
    """)
    op.drop_index(
        "ix_requirement_workflow_guest_grant",
        table_name="requirement_workflow_guest_bindings",
    )
    op.drop_column("artifact_publication_grants", "decision_only")
