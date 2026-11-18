"""Store the project estimate default independently of task revisions."""

import sqlalchemy as sa
from alembic import op

from app.models.comments import db_comment

revision = "ea61a82b95cd"
down_revision = "a64b9ce71df5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "estimate_method",
            sa.String(16),
            nullable=False,
            server_default="simple",
            comment=db_comment("標準見積方式", "新規の見積入力で使用する方式"),
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "estimate_method_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment=db_comment("標準見積方式の版", "方式設定の排他制御版"),
        ),
    )
    op.create_check_constraint(
        "ck_project_estimate_method",
        "projects",
        "estimate_method IN ('simple','pert','triangular','parametric','analogous')",
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM projects WHERE estimate_method_version > 1)"
        )
    ):
        raise RuntimeError("Project estimate settings are in use")
    op.drop_constraint("ck_project_estimate_method", "projects", type_="check")
    op.drop_column("projects", "estimate_method_version")
    op.drop_column("projects", "estimate_method")
