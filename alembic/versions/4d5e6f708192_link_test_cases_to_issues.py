"""テストケースと不具合タスクの多対多関連を追加する。"""

import sqlalchemy as sa

from alembic import op

revision = "4d5e6f708192"
down_revision = "3c4d5e6f7081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """不具合への関連と発見時の実行参照を保存する。"""
    op.create_table(
        "test_case_issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("test_cases.id"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column(
            "origin_execution_id",
            sa.Uuid(),
            sa.ForeignKey("test_executions.id"),
            nullable=True,
        ),
        sa.Column(
            "created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("case_id", "task_id"),
    )
    for column in ("case_id", "task_id", "origin_execution_id"):
        op.create_index(f"ix_test_case_issues_{column}", "test_case_issues", [column])


def downgrade() -> None:
    """関連を削除する。"""
    op.drop_table("test_case_issues")
