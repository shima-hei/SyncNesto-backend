"""案件内のワイヤーフレーム共通部品を版ごとに保存する。"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "4ce61bd208af"
down_revision = "7c19d8ab502e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存の画面・履歴には触れず、共通部品の版を格納する表を追加する。"""
    op.create_table(
        "wireframe_part_revisions",
        sa.Column("project_id", sa.Integer(), primary_key=True),
        sa.Column("part_id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.CheckConstraint("version >= 1", name="ck_wireframe_part_revision_version"),
    )


def downgrade() -> None:
    """固定版に埋め込んだ画面本文を保ち、共通部品の表を取り除く。"""
    op.drop_table("wireframe_part_revisions")
