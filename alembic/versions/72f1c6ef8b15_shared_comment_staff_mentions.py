"""Bind shared staff mentions to an author who has posted on that publication."""

import sqlalchemy as sa

from alembic import op

revision = "72f1c6ef8b15"
down_revision = "56b90ecf274d"
table = "shared_publication_comment_mentions"
primary = "shared_publication_comment_mentions_pkey"


def upgrade() -> None:
    op.add_column(table, sa.Column("recipient_ref_id", sa.Uuid()))
    op.add_column(table, sa.Column("public_author_comment_id", sa.Uuid()))
    op.execute(
        "UPDATE shared_publication_comment_mentions SET recipient_ref_id = grant_id"
    )
    op.alter_column(table, "recipient_ref_id", nullable=False)
    op.drop_constraint(primary, table, type_="primary")
    op.alter_column(table, "grant_id", nullable=True)
    op.create_primary_key(primary, table, ["comment_id", "recipient_ref_id"])
    op.create_foreign_key(
        "fk_shared_comment_mention_author",
        table,
        "shared_publication_comments",
        ["publication_id", "public_author_comment_id"],
        ["publication_id", "id"],
    )
    op.create_check_constraint(
        "ck_shared_comment_mention_recipient",
        table,
        "(grant_id IS NOT NULL AND public_author_comment_id IS NULL "
        "AND recipient_ref_id = grant_id) OR "
        "(grant_id IS NULL AND public_author_comment_id IS NOT NULL "
        "AND recipient_ref_id = public_author_comment_id)",
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE shared_publication_comment_mentions IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT FROM shared_publication_comment_mentions
            WHERE public_author_comment_id IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'shared staff mentions must be retained';
          END IF;
        END $$
    """)
    op.drop_constraint("ck_shared_comment_mention_recipient", table, type_="check")
    op.drop_constraint("fk_shared_comment_mention_author", table, type_="foreignkey")
    op.drop_constraint(primary, table, type_="primary")
    op.alter_column(table, "grant_id", nullable=False)
    op.create_primary_key(primary, table, ["comment_id", "grant_id"])
    op.drop_column(table, "public_author_comment_id")
    op.drop_column(table, "recipient_ref_id")
