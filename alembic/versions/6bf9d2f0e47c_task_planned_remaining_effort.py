"""Add independent planned and remaining effort on tasks."""

import sqlalchemy as sa
from alembic import op

from app.models.comments import db_comment

revision = "6bf9d2f0e47c"
down_revision = "25d310748dcc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "planned_minutes",
            sa.Integer(),
            nullable=True,
            comment=db_comment("計画分数", "このタスク自身に投入する計画工数"),
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "remaining_minutes",
            sa.Integer(),
            nullable=True,
            comment=db_comment("残工数の分数", "このタスク自身の作業に残る工数予測"),
        ),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM tasks "
            "WHERE planned_minutes IS NOT NULL OR remaining_minutes IS NOT NULL)"
        )
    ):
        raise RuntimeError("Planned or remaining task effort is in use")
    op.drop_column("tasks", "remaining_minutes")
    op.drop_column("tasks", "planned_minutes")
