"""create task management tables

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-06-23 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e0f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """タスク管理テーブルを作成する。"""
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("parent_task_id", sa.Integer(), nullable=True),
        sa.Column("task_code", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("task_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("priority", sa.String(length=50), nullable=False),
        sa.Column("assignee_id", sa.Integer(), nullable=True),
        sa.Column("reporter_id", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("actual_start_date", sa.Date(), nullable=True),
        sa.Column("actual_end_date", sa.Date(), nullable=True),
        sa.Column("progress_percent", sa.Integer(), nullable=False),
        sa.Column("estimated_minutes", sa.Integer(), nullable=True),
        sa.Column("actual_minutes", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["parent_task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "task_code",
            name="uq_tasks_project_task_code",
        ),
        comment="タスク: プロジェクト内の実作業タスクを管理するテーブル",
    )
    op.create_index(op.f("ix_tasks_id"), "tasks", ["id"], unique=False)
    op.create_index(op.f("ix_tasks_project_id"), "tasks", ["project_id"], unique=False)
    op.create_index(
        op.f("ix_tasks_parent_task_id"), "tasks", ["parent_task_id"], unique=False
    )
    op.create_index(op.f("ix_tasks_status"), "tasks", ["status"], unique=False)
    op.create_index(
        op.f("ix_tasks_assignee_id"),
        "tasks",
        ["assignee_id"],
        unique=False,
    )
    op.create_index(op.f("ix_tasks_due_date"), "tasks", ["due_date"], unique=False)

    op.create_table(
        "requirement_task_relations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("relation_type", sa.String(length=50), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["requirement_id"], ["requirements.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "requirement_id",
            "task_id",
            "relation_type",
            name="uq_requirement_task_relations",
        ),
        comment="要件タスク関連: 要件とタスクの関連を管理するテーブル",
    )
    op.create_index(
        op.f("ix_requirement_task_relations_id"),
        "requirement_task_relations",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_requirement_task_relations_requirement_id"),
        "requirement_task_relations",
        ["requirement_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_requirement_task_relations_task_id"),
        "requirement_task_relations",
        ["task_id"],
        unique=False,
    )

    op.create_table(
        "task_dependencies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("predecessor_task_id", sa.Integer(), nullable=False),
        sa.Column("successor_task_id", sa.Integer(), nullable=False),
        sa.Column("dependency_type", sa.String(length=50), nullable=False),
        sa.Column("lag_days", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["predecessor_task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["successor_task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "predecessor_task_id",
            "successor_task_id",
            "dependency_type",
            name="uq_task_dependencies",
        ),
        comment="タスク依存関係: タスク間の依存関係を管理するテーブル",
    )
    op.create_index(
        op.f("ix_task_dependencies_id"),
        "task_dependencies",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_task_dependencies_predecessor_task_id"),
        "task_dependencies",
        ["predecessor_task_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_task_dependencies_successor_task_id"),
        "task_dependencies",
        ["successor_task_id"],
        unique=False,
    )

    op.create_table(
        "milestones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        comment="マイルストーン: ガントチャート上の節目を管理するテーブル",
    )
    op.create_index(op.f("ix_milestones_id"), "milestones", ["id"], unique=False)
    op.create_index(
        op.f("ix_milestones_project_id"), "milestones", ["project_id"], unique=False
    )

    op.create_table(
        "boards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("board_type", sa.String(length=50), nullable=False),
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        comment="タスクボード: プロジェクト単位のカンバンボード設定を管理するテーブル",
    )
    op.create_index(op.f("ix_boards_id"), "boards", ["id"], unique=False)
    op.create_index(
        op.f("ix_boards_project_id"),
        "boards",
        ["project_id"],
        unique=False,
    )

    op.create_table(
        "board_columns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("board_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status_key", sa.String(length=50), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("wip_limit", sa.Integer(), nullable=True),
        sa.Column("is_done_column", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(["board_id"], ["boards.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("board_id", "status_key", name="uq_board_columns_status"),
        comment="ボード列: タスクボードの列設定を管理するテーブル",
    )
    op.create_index(op.f("ix_board_columns_id"), "board_columns", ["id"], unique=False)
    op.create_index(
        op.f("ix_board_columns_board_id"), "board_columns", ["board_id"], unique=False
    )

    op.create_table(
        "task_change_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("field_name", sa.String(length=100), nullable=True),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.Integer(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        comment="タスク変更履歴: タスク機能に関する変更履歴を管理するテーブル",
    )
    op.create_index(
        op.f("ix_task_change_logs_id"),
        "task_change_logs",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_task_change_logs_project_id"),
        "task_change_logs",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_task_change_logs_target_id"),
        "task_change_logs",
        ["target_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_task_change_logs_action"), "task_change_logs", ["action"], unique=False
    )
    op.create_index(
        op.f("ix_task_change_logs_changed_by"),
        "task_change_logs",
        ["changed_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_task_change_logs_changed_at"),
        "task_change_logs",
        ["changed_at"],
        unique=False,
    )


def downgrade() -> None:
    """タスク管理テーブルを削除する。"""
    op.drop_index(op.f("ix_task_change_logs_changed_at"), table_name="task_change_logs")
    op.drop_index(op.f("ix_task_change_logs_changed_by"), table_name="task_change_logs")
    op.drop_index(op.f("ix_task_change_logs_action"), table_name="task_change_logs")
    op.drop_index(op.f("ix_task_change_logs_target_id"), table_name="task_change_logs")
    op.drop_index(op.f("ix_task_change_logs_project_id"), table_name="task_change_logs")
    op.drop_index(op.f("ix_task_change_logs_id"), table_name="task_change_logs")
    op.drop_table("task_change_logs")
    op.drop_index(op.f("ix_board_columns_board_id"), table_name="board_columns")
    op.drop_index(op.f("ix_board_columns_id"), table_name="board_columns")
    op.drop_table("board_columns")
    op.drop_index(op.f("ix_boards_project_id"), table_name="boards")
    op.drop_index(op.f("ix_boards_id"), table_name="boards")
    op.drop_table("boards")
    op.drop_index(op.f("ix_milestones_project_id"), table_name="milestones")
    op.drop_index(op.f("ix_milestones_id"), table_name="milestones")
    op.drop_table("milestones")
    op.drop_index(
        op.f("ix_task_dependencies_successor_task_id"), table_name="task_dependencies"
    )
    op.drop_index(
        op.f("ix_task_dependencies_predecessor_task_id"),
        table_name="task_dependencies",
    )
    op.drop_index(op.f("ix_task_dependencies_id"), table_name="task_dependencies")
    op.drop_table("task_dependencies")
    op.drop_index(
        op.f("ix_requirement_task_relations_task_id"),
        table_name="requirement_task_relations",
    )
    op.drop_index(
        op.f("ix_requirement_task_relations_requirement_id"),
        table_name="requirement_task_relations",
    )
    op.drop_index(
        op.f("ix_requirement_task_relations_id"),
        table_name="requirement_task_relations",
    )
    op.drop_table("requirement_task_relations")
    op.drop_index(op.f("ix_tasks_due_date"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_assignee_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_status"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_parent_task_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_project_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_id"), table_name="tasks")
    op.drop_table("tasks")
