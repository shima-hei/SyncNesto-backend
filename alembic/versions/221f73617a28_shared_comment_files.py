"""Store private draft and posted attachments for shared comments."""

import sqlalchemy as sa
from alembic import op

revision = "221f73617a28"
down_revision = "a752cc934e21"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_grant_publication_id",
        "artifact_publication_grants",
        ["publication_id", "id"],
    )
    op.create_table(
        "shared_publication_comment_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "publication_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_publications.id"),
            nullable=False,
        ),
        sa.Column("comment_id", sa.Uuid()),
        sa.Column("position", sa.Integer()),
        sa.Column("grant_id", sa.Uuid()),
        sa.Column(
            "uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.ForeignKeyConstraint(
            ["publication_id", "comment_id"],
            [
                "shared_publication_comments.publication_id",
                "shared_publication_comments.id",
            ],
            name="fk_shared_comment_file_comment",
        ),
        sa.ForeignKeyConstraint(
            ["publication_id", "grant_id"],
            [
                "artifact_publication_grants.publication_id",
                "artifact_publication_grants.id",
            ],
            name="fk_shared_comment_file_grant",
        ),
        sa.CheckConstraint(
            "(comment_id IS NULL AND position IS NULL) OR "
            "(comment_id IS NOT NULL AND position IS NOT NULL AND position BETWEEN 0 AND 3)",
            name="ck_shared_comment_file_position",
        ),
        sa.UniqueConstraint(
            "comment_id", "position", name="uq_shared_comment_file_position"
        ),
    )
    op.create_index(
        "ix_shared_comment_file_drafts",
        "shared_publication_comment_files",
        ["publication_id", "uploaded_by", "created_at"],
        postgresql_where=sa.text("comment_id IS NULL"),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE shared_publication_comment_files IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT FROM shared_publication_comment_files) THEN
            RAISE EXCEPTION 'shared comment attachments must be retained';
          END IF;
        END $$
    """)
    op.drop_table("shared_publication_comment_files")
    op.drop_constraint(
        "uq_grant_publication_id", "artifact_publication_grants", type_="unique"
    )
