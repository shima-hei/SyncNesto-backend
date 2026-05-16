"""add key to roles

Revision ID: 6c4a9b2e8d31
Revises: 3f6b2c9d1a7e
Create Date: 2026-05-16 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6c4a9b2e8d31"
down_revision: Union[str, Sequence[str], None] = "3f6b2c9d1a7e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("roles", sa.Column("key", sa.String(length=100), nullable=True))
    op.execute("UPDATE roles SET key = name")
    op.alter_column("roles", "key", nullable=False)
    op.create_unique_constraint("uq_roles_key_scope", "roles", ["key", "scope"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_roles_key_scope", "roles", type_="unique")
    op.drop_column("roles", "key")
