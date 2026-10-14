"""Project内の構造化設計書・版・レビュー・関連を追加する。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e5a9c2f7b103"
down_revision = "d4f8b1e6a902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存のデータと権限割当を維持して独立した資源を追加する。"""
    op.create_table(
        "specifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        comment="設計書: Projectで共有するAPI・画面の仕様",
    )
    op.create_index("ix_specifications_project_id", "specifications", ["project_id"])
    op.create_table(
        "specification_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "specification_id",
            sa.Integer(),
            sa.ForeignKey("specifications.id"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("specification_id", "number"),
        comment="設計書版: 本文・状態のスナップショット",
    )
    op.create_table(
        "specification_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "specification_id",
            sa.Integer(),
            sa.ForeignKey("specifications.id"),
            nullable=False,
        ),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("field_path", sa.String(300), nullable=False),
        sa.Column("field_snapshot", sa.Text(), nullable=False),
        sa.Column("field_digest", sa.String(64), nullable=False),
        sa.Column("specification_version", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_resolved", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        comment="設計書指摘: 項目UUID・版を指定するレビュー指摘",
    )
    columns = {
        "requirement_id": "requirements",
        "task_id": "tasks",
        "test_item_id": "test_items",
        "related_specification_id": "specifications",
        "document_id": "project_documents",
    }
    op.create_table(
        "specification_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "specification_id",
            sa.Integer(),
            sa.ForeignKey("specifications.id"),
            nullable=False,
        ),
        *(
            sa.Column(
                name,
                sa.Uuid(),
                sa.ForeignKey(f"{table}.id"),
            )
            if name == "test_item_id"
            else sa.Column(name, sa.Integer(), sa.ForeignKey(f"{table}.id"))
            for name, table in columns.items()
        ),
        *(sa.UniqueConstraint("specification_id", name) for name in columns),
        sa.CheckConstraint(
            "num_nonnulls(requirement_id, task_id, test_item_id, related_specification_id, document_id) = 1",
            name="ck_specification_link_one_target",
        ),
        comment="設計書関連: 同一Project内の設計・要件・タスク・テスト・参考文書の関連",
    )
    for name in (
        "specification_revisions",
        "specification_comments",
        "specification_links",
    ):
        op.create_index(f"ix_{name}_specification_id", name, ["specification_id"])


def downgrade() -> None:
    """新しい設計書だけを削除する。"""
    for name in (
        "specification_links",
        "specification_comments",
        "specification_revisions",
        "specifications",
    ):
        op.drop_table(name)
