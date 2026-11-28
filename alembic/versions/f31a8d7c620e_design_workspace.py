"""既存設計のID・版を保ったまま、グループ・図の配置を追加する。"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "f31a8d7c620e"
down_revision = "d84b9a6e2f10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存設計の行と保存済みスナップショットを変更しない追加移行。"""
    op.create_unique_constraint(
        "uq_specifications_project_id_id", "specifications", ["project_id", "id"]
    )
    op.create_table(
        "design_collections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("parent_id", sa.Uuid()),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("configuration", JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_design_collections_project_id_id"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "parent_id"],
            ["design_collections.project_id", "design_collections.id"],
            name="fk_design_collections_parent_project",
        ),
        sa.CheckConstraint(
            "kind IN ('group', 'api_set', 'db_model')",
            name="ck_design_collections_kind",
        ),
    )
    op.create_table(
        "design_memberships",
        sa.Column("project_id", sa.Integer(), primary_key=True),
        sa.Column("specification_id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Uuid(), primary_key=True),
        sa.ForeignKeyConstraint(
            ["project_id", "group_id"],
            ["design_collections.project_id", "design_collections.id"],
            name="fk_design_memberships_group_project",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "specification_id"],
            ["specifications.project_id", "specifications.id"],
            name="fk_design_memberships_specification_project",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "design_placements",
        sa.Column("project_id", sa.Integer(), primary_key=True),
        sa.Column("specification_id", sa.Integer(), primary_key=True),
        sa.Column("view_key", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.Uuid()),
        sa.Column("x", sa.Integer(), nullable=False),
        sa.Column("y", sa.Integer(), nullable=False),
        sa.Column("hidden", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "group_id"],
            ["design_collections.project_id", "design_collections.id"],
            name="fk_design_placements_group_project",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "specification_id"],
            ["specifications.project_id", "specifications.id"],
            name="fk_design_placements_specification_project",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "x >= 0 AND y >= 0", name="ck_design_placements_coordinates"
        ),
    )


def downgrade() -> None:
    """新しい設計データがある場合は先に保全を求める。"""
    if not op.get_context().as_sql:
        op.execute("""
            DO $$
            BEGIN
                IF EXISTS (SELECT FROM design_collections)
                   OR EXISTS (SELECT FROM design_placements)
                   OR EXISTS (SELECT FROM specifications WHERE kind = 'database'
                       OR content ?|
                           ARRAY['operation', 'artboards', 'transitions', 'mockups']
                   ) THEN
                    RAISE EXCEPTION 'Preserve design workspace data before downgrade';
                END IF;
            END
            $$;
        """)
    op.drop_table("design_placements")
    op.drop_table("design_memberships")
    op.drop_table("design_collections")
    op.drop_constraint(
        "uq_specifications_project_id_id", "specifications", type_="unique"
    )
