"""テスト設計の追跡、議論、実行証跡を追加する。"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "3c4d5e6f7081"
down_revision = "2b3c4d5e6f70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """履歴を保持する列と専用エンティティを作成する。"""
    op.execute(sa.text("""
        INSERT INTO permissions (code, description, created_at, updated_at)
        VALUES ('test_plan:comment', 'テスト設計書にコメントする', now(), now())
        ON CONFLICT (code) DO NOTHING
    """))
    op.execute(sa.text("""
        INSERT INTO role_permissions (role_id, permission_id, created_at)
        SELECT roles.id, permissions.id, now()
        FROM roles CROSS JOIN permissions
        WHERE permissions.code = 'test_plan:comment'
          AND roles.key IN ('system_admin', 'project_admin', 'manager', 'member')
        ON CONFLICT (role_id, permission_id) DO NOTHING
    """))
    for table in (
        "test_designs",
        "test_pattern_tables",
        "test_items",
        "test_factors",
        "test_factor_levels",
        "test_patterns",
        "test_expected_values",
    ):
        op.add_column(table, sa.Column("deleted_at", sa.DateTime(timezone=True)))

    op.create_table(
        "requirement_test_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "requirement_id",
            sa.Integer(),
            sa.ForeignKey("requirements.id"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Uuid(), sa.ForeignKey("test_items.id"), nullable=False),
        sa.Column(
            "created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("requirement_id", "item_id"),
    )
    op.create_index(
        "ix_requirement_test_items_requirement_id",
        "requirement_test_items",
        ["requirement_id"],
    )
    op.create_index(
        "ix_requirement_test_items_item_id", "requirement_test_items", ["item_id"]
    )

    op.create_table(
        "test_design_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "design_id", sa.Integer(), sa.ForeignKey("test_designs.id"), nullable=False
        ),
        sa.Column("target_type", sa.String(40), nullable=False),
        sa.Column("target_id", sa.Uuid()),
        sa.Column("field", sa.String(100)),
        sa.Column("target_snapshot", JSONB(), nullable=False),
        sa.Column(
            "parent_comment_id", sa.Integer(), sa.ForeignKey("test_design_comments.id")
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "is_resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    for column in ("design_id", "target_type", "target_id", "parent_comment_id"):
        op.create_index(
            f"ix_test_design_comments_{column}", "test_design_comments", [column]
        )
    op.create_table(
        "test_design_comment_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "comment_id",
            sa.Integer(),
            sa.ForeignKey("test_design_comments.id"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("old_value", JSONB()),
        sa.Column("new_value", JSONB()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_test_design_comment_changes_comment_id",
        "test_design_comment_changes",
        ["comment_id"],
    )

    op.create_table(
        "test_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("test_cases.id"), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("actual_result", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("source", JSONB(), nullable=False),
        sa.Column("executed_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("case_id", "run_number"),
    )
    op.create_index("ix_test_executions_case_id", "test_executions", ["case_id"])
    op.execute(
        sa.text("""
        INSERT INTO test_executions
            (id, case_id, run_number, status, actual_result, notes,
             source, executed_by, executed_at)
        SELECT gen_random_uuid(), id, 1, status, actual_result, notes,
               source, executed_by, executed_at
        FROM test_cases WHERE executed_at IS NOT NULL
    """)
    )
    op.create_table(
        "test_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "execution_id",
            sa.Uuid(),
            sa.ForeignKey("test_executions.id"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(500), nullable=False, unique=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column(
            "uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_test_evidence_execution_id", "test_evidence", ["execution_id"])


def downgrade() -> None:
    """追加したエンティティと論理削除列を除く。"""
    op.execute(sa.text("""
        DELETE FROM role_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions WHERE code = 'test_plan:comment'
        )
    """))
    op.execute(sa.text("DELETE FROM permissions WHERE code = 'test_plan:comment'"))
    op.drop_table("test_evidence")
    op.drop_table("test_executions")
    op.drop_table("test_design_comment_changes")
    op.drop_table("test_design_comments")
    op.drop_table("requirement_test_items")
    for table in (
        "test_expected_values",
        "test_patterns",
        "test_factor_levels",
        "test_factors",
        "test_items",
        "test_pattern_tables",
        "test_designs",
    ):
        op.drop_column(table, "deleted_at")
