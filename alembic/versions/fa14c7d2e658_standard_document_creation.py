"""標準章付き文書作成の再送記録を追加する。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "fa14c7d2e658"
down_revision = "f9b3c6e1d547"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "requirement_document_creation_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("requirement_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "project_id",
            "actor_id",
            "request_key",
            name="uq_requirement_document_creation_request",
        ),
        comment="標準章付き作成結果：本人とProjectごとの再送結果",
    )
    op.create_index(
        "ix_requirement_document_creation_receipts_document_id",
        "requirement_document_creation_receipts",
        ["document_id"],
    )
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='syncnesto_app') THEN
      REVOKE ALL ON requirement_document_creation_receipts FROM syncnesto_app;
      GRANT SELECT, INSERT ON requirement_document_creation_receipts TO syncnesto_app;
      GRANT USAGE, SELECT ON SEQUENCE requirement_document_creation_receipts_id_seq TO syncnesto_app;
    END IF; END $$;""")


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS(SELECT FROM requirement_document_creation_receipts)")
    ):
        raise RuntimeError(
            "作成結果と再送記録を保持し、利用済みの台帳は削除しないでください"
        )
    op.drop_table("requirement_document_creation_receipts")
