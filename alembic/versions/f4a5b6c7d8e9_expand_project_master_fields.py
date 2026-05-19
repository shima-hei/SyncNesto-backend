"""expand project master fields

Revision ID: f4a5b6c7d8e9
Revises: e2f3a4b5c6d7
Create Date: 2026-05-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, Sequence[str], None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "projects",
        sa.Column("project_code", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("status", sa.String(length=50), nullable=False, server_default="active"),
    )
    op.add_column("projects", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column("projects", sa.Column("end_date", sa.Date(), nullable=True))
    op.add_column("projects", sa.Column("created_by", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("updated_by", sa.Integer(), nullable=True))

    op.execute("UPDATE projects SET project_code = 'PRJ-' || id")
    op.alter_column(
        "projects",
        "project_code",
        existing_type=sa.String(length=100),
        nullable=False,
    )
    op.create_index(
        op.f("ix_projects_project_code"),
        "projects",
        ["project_code"],
        unique=True,
    )
    op.create_foreign_key(
        "fk_projects_created_by_users",
        "projects",
        "users",
        ["created_by"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_projects_updated_by_users",
        "projects",
        "users",
        ["updated_by"],
        ["id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("fk_projects_updated_by_users", "projects", type_="foreignkey")
    op.drop_constraint("fk_projects_created_by_users", "projects", type_="foreignkey")
    op.drop_index(op.f("ix_projects_project_code"), table_name="projects")
    op.drop_column("projects", "updated_by")
    op.drop_column("projects", "created_by")
    op.drop_column("projects", "end_date")
    op.drop_column("projects", "start_date")
    op.drop_column("projects", "status")
    op.drop_column("projects", "project_code")
