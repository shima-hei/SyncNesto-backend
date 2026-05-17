"""add profile fields to users

Revision ID: c7e9a1b2d4f6
Revises: a4d8e2f1c9b0
Create Date: 2026-05-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c7e9a1b2d4f6"
down_revision: Union[str, Sequence[str], None] = "a4d8e2f1c9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("department", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("position", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("avatar_url", sa.String(length=1000), nullable=True))
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("users", "is_active", server_default=None)
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("users", sa.Column("created_by", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("updated_by", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_users_created_by_users",
        "users",
        "users",
        ["created_by"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_users_updated_by_users",
        "users",
        "users",
        ["updated_by"],
        ["id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("fk_users_updated_by_users", "users", type_="foreignkey")
    op.drop_constraint("fk_users_created_by_users", "users", type_="foreignkey")
    op.drop_column("users", "updated_by")
    op.drop_column("users", "created_by")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "is_active")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "position")
    op.drop_column("users", "department")
