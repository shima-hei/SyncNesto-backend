"""ゲストに公開する案件概要を独立して保存する。"""

import sqlalchemy as sa

from alembic import op

revision = "8b31a94f6c20"
down_revision = "6e471c52f102"


def upgrade() -> None:
    """既存案件やゲストの可視性を変えずに追加する。"""
    op.create_table(
        "guest_project_profiles",
        sa.Column(
            "project_id", sa.Integer(), sa.ForeignKey("projects.id"), primary_key=True
        ),
        sa.Column("name", sa.String(255)),
        sa.Column("description", sa.Text()),
        sa.Column("starts_on", sa.Date()),
        sa.Column("ends_on", sa.Date()),
        sa.Column("contact", sa.String(255)),
        sa.Column("announcement", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "version > 0 AND (starts_on IS NULL OR ends_on IS NULL OR starts_on <= ends_on)",
            name="ck_guest_project_profile_version_and_dates",
        ),
    )
    op.add_column(
        "artifact_publication_grants", sa.Column("download_allowed", sa.Boolean())
    )
    op.add_column(
        "artifact_publication_grants",
        sa.Column(
            "comment_allowed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_table(
        "shared_publication_comments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "publication_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_publications.id"),
            nullable=False,
        ),
        sa.Column("parent_id", sa.Uuid()),
        sa.Column("node_id", sa.Uuid()),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("author_label", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "publication_id", "id", name="uq_shared_comment_publication"
        ),
        sa.ForeignKeyConstraint(
            ["publication_id", "parent_id"],
            [
                "shared_publication_comments.publication_id",
                "shared_publication_comments.id",
            ],
            name="fk_shared_comment_parent",
        ),
        sa.ForeignKeyConstraint(
            ["publication_id", "node_id"],
            [
                "artifact_publication_nodes.publication_id",
                "artifact_publication_nodes.id",
            ],
            name="fk_shared_comment_node",
        ),
        sa.CheckConstraint(
            "id <> parent_id AND node_id IS NULL OR parent_id IS NULL",
            name="ck_shared_comment_reply_shape",
        ),
    )
    op.create_index(
        "ix_shared_comment_thread",
        "shared_publication_comments",
        ["publication_id", "parent_id", "created_at", "id"],
    )


def downgrade() -> None:
    """公開用の設定を意図せず失わないよう、記録済みのDBでは中断する。"""
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM guest_project_profiles)
             OR EXISTS (SELECT 1 FROM artifact_publication_grants
                        WHERE download_allowed IS NOT NULL OR comment_allowed)
             OR EXISTS (SELECT 1 FROM shared_publication_comments) THEN
            RAISE EXCEPTION 'guest project profiles and sharing policies must be retained';
          END IF;
        END $$;
    """)
    op.drop_table("shared_publication_comments")
    op.drop_column("artifact_publication_grants", "comment_allowed")
    op.drop_column("artifact_publication_grants", "download_allowed")
    op.drop_table("guest_project_profiles")
