"""add task comments and tags

Revision ID: 0b1c2d3e4f5a
Revises: f1a2b3c4d5e6
Create Date: 2026-06-24 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0b1c2d3e4f5a"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """タスクコメントテーブルとタスクタグカラムを追加する。"""
    op.add_column(
        "tasks",
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )

    op.create_table(
        "task_comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("parent_comment_id", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_resolved", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["parent_comment_id"], ["task_comments.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        comment="タスクコメント: タスクに対するコメントと解決状態を管理するテーブル",
    )
    op.create_index(op.f("ix_task_comments_id"), "task_comments", ["id"])
    op.create_index(op.f("ix_task_comments_task_id"), "task_comments", ["task_id"])
    op.create_index(
        op.f("ix_task_comments_parent_comment_id"),
        "task_comments",
        ["parent_comment_id"],
    )


def downgrade() -> None:
    """タスクコメントテーブルとタスクタグカラムを削除する。"""
    op.drop_index(
        op.f("ix_task_comments_parent_comment_id"),
        table_name="task_comments",
    )
    op.drop_index(op.f("ix_task_comments_task_id"), table_name="task_comments")
    op.drop_index(op.f("ix_task_comments_id"), table_name="task_comments")
    op.drop_table("task_comments")
    op.drop_column("tasks", "tags")
