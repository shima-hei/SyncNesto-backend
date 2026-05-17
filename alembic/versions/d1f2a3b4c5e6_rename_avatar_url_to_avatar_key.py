"""rename avatar url to avatar key

Revision ID: d1f2a3b4c5e6
Revises: c7e9a1b2d4f6
Create Date: 2026-05-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d1f2a3b4c5e6"
down_revision: Union[str, Sequence[str], None] = "c7e9a1b2d4f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("users", "avatar_url", new_column_name="avatar_key")


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("users", "avatar_key", new_column_name="avatar_url")
