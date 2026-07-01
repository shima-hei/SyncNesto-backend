"""create drafts

Revision ID: 3e4f5a6b7c8d
Revises: 2d3e4f5a6b7c
Create Date: 2026-07-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.models.comments import db_comment

revision: str = "3e4f5a6b7c8d"
down_revision: str | Sequence[str] | None = "2d3e4f5a6b7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """draftsテーブルを作成する。"""
    op.create_table(
        "drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("scope_key", sa.String(length=255), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "scope_key",
            name="uq_drafts_owner_scope",
        ),
        comment=db_comment(
            "下書き",
            "フォーム入力中の下書きをユーザーとスコープ単位で管理するテーブル",
        ),
    )
    op.create_index(op.f("ix_drafts_id"), "drafts", ["id"])
    op.create_index(op.f("ix_drafts_owner_user_id"), "drafts", ["owner_user_id"])
    op.create_index(op.f("ix_drafts_project_id"), "drafts", ["project_id"])
    op.create_index(op.f("ix_drafts_resource_type"), "drafts", ["resource_type"])
    op.create_index(op.f("ix_drafts_scope_key"), "drafts", ["scope_key"])


def downgrade() -> None:
    """draftsテーブルを削除する。"""
    op.drop_index(op.f("ix_drafts_scope_key"), table_name="drafts")
    op.drop_index(op.f("ix_drafts_resource_type"), table_name="drafts")
    op.drop_index(op.f("ix_drafts_project_id"), table_name="drafts")
    op.drop_index(op.f("ix_drafts_owner_user_id"), table_name="drafts")
    op.drop_index(op.f("ix_drafts_id"), table_name="drafts")
    op.drop_table("drafts")
