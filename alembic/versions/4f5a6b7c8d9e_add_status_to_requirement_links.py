"""add status to requirement links

Revision ID: 4f5a6b7c8d9e
Revises: 3e4f5a6b7c8d
Create Date: 2026-07-02 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.comments import db_comment

revision: str = "4f5a6b7c8d9e"
down_revision: str | Sequence[str] | None = "3e4f5a6b7c8d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """requirement_linksに成果物状態を追加する。"""
    op.add_column(
        "requirement_links",
        sa.Column(
            "status",
            sa.String(length=50),
            server_default="unknown",
            nullable=False,
            comment=db_comment(
                "成果物状態",
                "unknown/not_started/in_progress/completed/verifiedなどの成果物状態",
            ),
        ),
    )
    op.alter_column("requirement_links", "status", server_default=None)


def downgrade() -> None:
    """requirement_linksから成果物状態を削除する。"""
    op.drop_column("requirement_links", "status")
