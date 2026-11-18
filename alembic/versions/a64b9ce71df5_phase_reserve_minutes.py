"""Keep phase-level reserve separate from task effort."""

import sqlalchemy as sa
from alembic import op

from app.models.comments import db_comment

revision = "a64b9ce71df5"
down_revision = "2a641bd987ce"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_phases",
        sa.Column(
            "reserve_minutes",
            sa.Integer(),
            nullable=True,
            comment=db_comment("工程予備工数", "タスク工数と別枠で確保する分数"),
        ),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM project_phases WHERE reserve_minutes IS NOT NULL)"
        )
    ):
        raise RuntimeError("Phase reserves are in use")
    op.drop_column("project_phases", "reserve_minutes")
