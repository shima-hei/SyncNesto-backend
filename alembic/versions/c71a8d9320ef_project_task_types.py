"""案件別の独自タスク種別を追加し、既存Taskを変更しない。"""

import sqlalchemy as sa

from alembic import op

revision = "c71a8d9320ef"
down_revision = "bc37e52177d2"
branch_labels = None
depends_on = None


def upgrade():
    """空の管理テーブルだけを追加する。"""
    op.create_table(
        "project_task_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False
        ),
        sa.Column("key", sa.String(50), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("name_key", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.UniqueConstraint("project_id", "key", name="uq_project_task_types_key"),
        sa.UniqueConstraint(
            "project_id", "name_key", name="uq_project_task_types_name"
        ),
        comment="案件別タスク種別: 非表示後も内部キーと名前を保持",
    )
    op.create_index(
        "ix_project_task_types_project_id", "project_task_types", ["project_id"]
    )


def downgrade():
    """使用したカタログを黙って失わない。"""
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT FROM project_task_types)")):
        raise RuntimeError("独自種別の参照を保持してください")
    op.drop_table("project_task_types")
