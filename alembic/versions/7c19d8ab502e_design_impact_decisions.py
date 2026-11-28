"""設計の版別に変更影響の確認結果を記録する。"""

import sqlalchemy as sa

from alembic import op

revision = "7c19d8ab502e"
down_revision = "f31a8d7c620e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存版に影響を与えず確認状態の表を作成する。"""
    op.create_table(
        "design_impact_decisions",
        sa.Column("project_id", sa.Integer(), primary_key=True),
        sa.Column("specification_id", sa.Integer(), primary_key=True),
        sa.Column("source_version", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "specification_id"],
            ["specifications.project_id", "specifications.id"],
            name="fk_design_impact_specification_project",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('confirmed', 'needs_action')", name="ck_design_impact_status"
        ),
    )


def downgrade() -> None:
    """図や設計本体のデータを保ち、確認状態の表だけを戻す。"""
    op.drop_table("design_impact_decisions")
