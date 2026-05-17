"""set default avatar key

Revision ID: e2f3a4b5c6d7
Revises: d1f2a3b4c5e6
Create Date: 2026-05-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "d1f2a3b4c5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_AVATAR_KEY = "default-avatar.png"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        sa.text(
            "UPDATE users SET avatar_key = :default_avatar_key "
            "WHERE avatar_key IS NULL"
        ).bindparams(default_avatar_key=DEFAULT_AVATAR_KEY)
    )
    op.alter_column(
        "users",
        "avatar_key",
        server_default=DEFAULT_AVATAR_KEY,
        existing_type=sa.String(length=1000),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "users",
        "avatar_key",
        server_default=None,
        existing_type=sa.String(length=1000),
    )
