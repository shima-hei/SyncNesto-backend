"""add version to projects

Revision ID: 9b7c1d2e3f44
Revises: 6c4a9b2e8d31
Create Date: 2026-05-16 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9b7c1d2e3f44"
down_revision: Union[str, Sequence[str], None] = "6c4a9b2e8d31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "projects",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column("projects", "version", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("projects", "version")
