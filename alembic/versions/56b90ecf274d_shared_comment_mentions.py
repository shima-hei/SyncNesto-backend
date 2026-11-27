"""Bind shared comment mentions to the recipient's publication grant."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "56b90ecf274d"
down_revision = "221f73617a28"


def upgrade() -> None:
    op.create_table(
        "shared_publication_comment_mentions",
        sa.Column("publication_id", sa.Uuid(), nullable=False),
        sa.Column("comment_id", sa.Uuid(), primary_key=True),
        sa.Column("grant_id", sa.Uuid(), primary_key=True),
        sa.Column("occurrences", JSONB, nullable=False),
        sa.ForeignKeyConstraint(
            ["publication_id", "comment_id"],
            [
                "shared_publication_comments.publication_id",
                "shared_publication_comments.id",
            ],
            name="fk_shared_comment_mention_comment",
        ),
        sa.ForeignKeyConstraint(
            ["publication_id", "grant_id"],
            [
                "artifact_publication_grants.publication_id",
                "artifact_publication_grants.id",
            ],
            name="fk_shared_comment_mention_grant",
        ),
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE shared_publication_comment_mentions IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT FROM shared_publication_comment_mentions) THEN
            RAISE EXCEPTION 'shared comment mentions must be retained';
          END IF;
        END $$
    """)
    op.drop_table("shared_publication_comment_mentions")
