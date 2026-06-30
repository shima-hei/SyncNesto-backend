"""add user type to users

Revision ID: 1c2d3e4f5a6b
Revises: 0b1c2d3e4f5a
Create Date: 2026-06-30 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1c2d3e4f5a6b"
down_revision: str | None = "0b1c2d3e4f5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """usersにユーザー区分を追加する。"""
    op.add_column(
        "users",
        sa.Column(
            "user_type",
            sa.String(length=50),
            server_default="internal",
            nullable=False,
            comment="ユーザー区分: 社内ユーザーまたはゲストユーザーなどの横断的なユーザー区分",
        ),
    )


def downgrade() -> None:
    """usersからユーザー区分を削除する。"""
    op.drop_column("users", "user_type")
