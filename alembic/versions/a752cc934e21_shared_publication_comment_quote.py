"""Bind shared comment quotations to a node on the fixed publication."""

from alembic import op
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB

revision = "a752cc934e21"
down_revision = "90ce274d18fa"


def upgrade() -> None:
    op.add_column(
        "shared_publication_comments", Column("anchor", JSONB(none_as_null=True))
    )
    op.create_check_constraint(
        "ck_shared_comment_quote_shape",
        "shared_publication_comments",
        "anchor IS NULL OR (parent_id IS NULL AND node_id IS NOT NULL "
        "AND jsonb_typeof(anchor) = 'object')",
    )


def downgrade() -> None:
    op.execute("LOCK TABLE shared_publication_comments IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT FROM shared_publication_comments WHERE anchor IS NOT NULL) THEN
            RAISE EXCEPTION 'shared comment quotations must be retained';
          END IF;
        END $$
    """)
    op.drop_constraint(
        "ck_shared_comment_quote_shape", "shared_publication_comments", type_="check"
    )
    op.drop_column("shared_publication_comments", "anchor")
