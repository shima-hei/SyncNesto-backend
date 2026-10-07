"""add ephemeral demo lifecycle and ownership

Revision ID: 4c7c0372061a
Revises: e39b52d8c013
Create Date: 2026-10-07 18:08:04.008998

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "4c7c0372061a"
down_revision: Union[str, Sequence[str], None] = "e39b52d8c013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """一時環境用の追加テーブルだけを変更する。"""
    op.create_table(
        "demo_start_budgets",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("window_start", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key", "window_start"),
    )
    op.create_table(
        "demo_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=30), nullable=True),
        sa.Column("cleanup_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleanup_attempts", sa.Integer(), nullable=False),
        sa.Column("cleanup_error", sa.String(length=30), nullable=True),
        sa.Column("cleaned_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
        sa.UniqueConstraint("tenant_id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        op.f("ix_demo_sessions_cleanup_after"),
        "demo_sessions",
        ["cleanup_after"],
        unique=False,
    )
    op.create_index(
        op.f("ix_demo_sessions_expires_at"),
        "demo_sessions",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_demo_sessions_status"), "demo_sessions", ["status"], unique=False
    )
    op.create_table(
        "demo_owned_users",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("demo_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["demo_id"],
            ["demo_sessions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        op.f("ix_demo_owned_users_demo_id"),
        "demo_owned_users",
        ["demo_id"],
        unique=False,
    )
    op.create_table(
        "demo_uploads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("demo_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=1000), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["demo_id"],
            ["demo_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index(
        op.f("ix_demo_uploads_demo_id"), "demo_uploads", ["demo_id"], unique=False
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    """一時環境用の追加テーブルだけを変更する。"""
    op.drop_index(op.f("ix_demo_uploads_demo_id"), table_name="demo_uploads")
    op.drop_table("demo_uploads")
    op.drop_index(op.f("ix_demo_owned_users_demo_id"), table_name="demo_owned_users")
    op.drop_table("demo_owned_users")
    op.drop_index(op.f("ix_demo_sessions_status"), table_name="demo_sessions")
    op.drop_index(op.f("ix_demo_sessions_expires_at"), table_name="demo_sessions")
    op.drop_index(op.f("ix_demo_sessions_cleanup_after"), table_name="demo_sessions")
    op.drop_table("demo_sessions")
    op.drop_table("demo_start_budgets")
    # ### end Alembic commands ###
