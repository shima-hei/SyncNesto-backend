"""Store parametric and analogous inputs on immutable estimate revisions."""

import sqlalchemy as sa
from alembic import op

from app.models.comments import db_comment

revision = "2a641bd987ce"
down_revision = "6bf9d2f0e47c"
branch_labels = None
depends_on = None

TABLE = "task_estimate_revisions"


def upgrade() -> None:
    for column in (
        sa.Column(
            "quantity",
            sa.Numeric(12, 4),
            nullable=True,
            comment=db_comment("数量", "見積対象の数量"),
        ),
        sa.Column(
            "unit_hours",
            sa.Numeric(12, 4),
            nullable=True,
            comment=db_comment("単位工数", "数量1単位あたりの時間"),
        ),
        sa.Column(
            "reference_work",
            sa.String(200),
            nullable=True,
            comment=db_comment("類似作業", "参照した作業の名称"),
        ),
        sa.Column(
            "reference_minutes",
            sa.Integer,
            nullable=True,
            comment=db_comment("参照分数", "類似作業の工数"),
        ),
        sa.Column(
            "factor",
            sa.Numeric(12, 4),
            nullable=True,
            comment=db_comment("補正係数", "参照工数に掛ける倍率"),
        ),
    ):
        op.add_column(TABLE, column)


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM task_estimate_revisions WHERE method IN ('parametric', 'analogous'))"
        )
    ):
        raise RuntimeError("Estimate revisions use parametric or analogous inputs")
    for name in (
        "factor",
        "reference_minutes",
        "reference_work",
        "unit_hours",
        "quantity",
    ):
        op.drop_column(TABLE, name)
