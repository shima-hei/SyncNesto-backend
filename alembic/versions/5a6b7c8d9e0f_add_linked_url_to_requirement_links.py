"""add linked_url to requirement_links

Revision ID: 5a6b7c8d9e0f
Revises: 4f5a6b7c8d9e
Create Date: 2026-07-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "5a6b7c8d9e0f"
down_revision: str | Sequence[str] | None = "4f5a6b7c8d9e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """成果物URL列を追加する。"""
    op.add_column(
        "requirement_links",
        sa.Column(
            "linked_url",
            sa.String(length=2048),
            nullable=True,
            comment="成果物本体や参照先を開くためのURL",
        ),
    )


def downgrade() -> None:
    """成果物URL列を削除する。"""
    op.drop_column("requirement_links", "linked_url")
