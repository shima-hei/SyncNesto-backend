"""add version to users and project members

Revision ID: a4d8e2f1c9b0
Revises: 9b7c1d2e3f44
Create Date: 2026-05-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a4d8e2f1c9b0"
down_revision: Union[str, Sequence[str], None] = "9b7c1d2e3f44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column("users", "version", server_default=None)
    op.add_column(
        "project_members",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column("project_members", "version", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("project_members", "version")
    op.drop_column("users", "version")
