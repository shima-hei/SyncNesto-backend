"""Store the project navigation display settings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models.comments import db_comment

revision = "a7173cc00475"
down_revision = "ea61a82b95cd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "navigation",
            postgresql.JSONB(none_as_null=True),
            nullable=True,
            comment=db_comment("案件ナビゲーション", "案件内メニューの順序と表示位置"),
        ),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM projects WHERE navigation IS NOT NULL)")
    ):
        raise RuntimeError("Project navigation settings are in use")
    op.drop_column("projects", "navigation")
