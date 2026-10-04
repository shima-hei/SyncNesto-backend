"""Add shared request counters for serverless API instances.

Revision ID: b94f7d20c315
Revises: a83d6e91b402
"""

from alembic import op
import sqlalchemy as sa

revision = "b94f7d20c315"
down_revision = "a83d6e91b402"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """共有カウンターと期限切れ削除用の索引を作る。"""
    op.create_table(
        "request_limits",
        sa.Column(
            "key",
            sa.String(80),
            primary_key=True,
            comment="用途とIPのHMAC、または全体上限の識別子",
        ),
        sa.Column(
            "window_start",
            sa.Integer(),
            nullable=False,
            comment="UTCのUnix秒で表す一分間の開始時刻",
        ),
        sa.Column("count", sa.Integer(), nullable=False, comment="時間枠内の要求回数"),
        comment="一分間の共有カウンター",
    )
    op.create_index(
        "ix_request_limits_window_start", "request_limits", ["window_start"]
    )


def downgrade() -> None:
    """カウンターを削除する。"""
    op.drop_index("ix_request_limits_window_start", table_name="request_limits")
    op.drop_table("request_limits")
