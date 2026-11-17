"""Store optional project phase display details."""

import sqlalchemy as sa

from alembic import op

revision = "0a27d9c7859b"
down_revision = "ba34f18c29d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project_phases", sa.Column("color", sa.String(12), nullable=True))
    op.add_column(
        "project_phases", sa.Column("description", sa.String(2000), nullable=True)
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM project_phases "
            "WHERE color IS NOT NULL OR description IS NOT NULL)"
        )
    ):
        raise RuntimeError("Phase details are in use")
    op.drop_column("project_phases", "description")
    op.drop_column("project_phases", "color")
