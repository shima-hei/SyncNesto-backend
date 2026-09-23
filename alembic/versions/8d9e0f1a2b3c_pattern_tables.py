"""用途別パターン表と単独ケースを追加する。"""

from uuid import NAMESPACE_URL, uuid5

from alembic import op
import sqlalchemy as sa

revision = "8d9e0f1a2b3c"
down_revision = "7c8d9e0f1a2b"
branch_labels = None
depends_on = None


def upgrade():
    """既存マトリクスを既定の表へ移し、結果を保持する。"""
    op.create_table(
        "test_pattern_tables",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "design_id",
            sa.Integer(),
            sa.ForeignKey("test_designs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        comment="用途ごとに独立したパターン表",
    )
    op.create_index(
        "ix_test_pattern_tables_design_id", "test_pattern_tables", ["design_id"]
    )
    for table, column, action in [
        ("test_items", "pattern_table_id", "SET NULL"),
        ("test_factors", "table_id", "CASCADE"),
        ("test_patterns", "table_id", "CASCADE"),
        ("test_expected_values", "table_id", "CASCADE"),
    ]:
        op.add_column(table, sa.Column(column, sa.Uuid(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_{column}_table",
            table,
            "test_pattern_tables",
            [column],
            ["id"],
            ondelete=action,
        )
        op.create_index(f"ix_{table}_{column}", table, [column])
    op.add_column("test_cases", sa.Column("source_key", sa.String(100), nullable=True))
    op.create_unique_constraint(
        "uq_test_cases_design_source", "test_cases", ["design_id", "source_key"]
    )
    conn = op.get_bind()
    for design_id in conn.execute(
        sa.text(
            "SELECT id FROM test_designs WHERE EXISTS (SELECT 1 FROM test_patterns WHERE design_id=test_designs.id) OR EXISTS (SELECT 1 FROM test_factors WHERE design_id=test_designs.id) OR EXISTS (SELECT 1 FROM test_expected_values WHERE design_id=test_designs.id)"
        )
    ).scalars():
        table_id = uuid5(
            NAMESPACE_URL, f"syncnesto/design/{design_id}/legacy-pattern-table"
        )
        conn.execute(
            sa.text(
                "UPDATE test_designs SET version=version+1, updated_at=now() WHERE id=:design"
            ),
            {"design": design_id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO test_pattern_tables (id,design_id,position,name) VALUES (:id,:design,0,'既存パターン表')"
            ),
            {"id": table_id, "design": design_id},
        )
        for table in ["test_factors", "test_patterns", "test_expected_values"]:
            conn.execute(
                sa.text(f"UPDATE {table} SET table_id=:id WHERE design_id=:design"),
                {"id": table_id, "design": design_id},
            )
        conn.execute(
            sa.text(
                "UPDATE test_items SET pattern_table_id=:id WHERE design_id=:design AND id IN (SELECT item_id FROM test_item_patterns WHERE design_id=:design)"
            ),
            {"id": table_id, "design": design_id},
        )
        conn.execute(
            sa.text(
                "UPDATE test_cases SET source_key=(source->'item'->>'id') || ':' || (source->'pattern'->>'id') WHERE design_id=:design AND item_pattern_id IS NOT NULL"
            ),
            {"design": design_id},
        )
    conn.execute(sa.text("DELETE FROM test_item_patterns"))


def downgrade():
    """追加の表構造を取り除く。"""
    op.drop_constraint("uq_test_cases_design_source", "test_cases", type_="unique")
    op.drop_column("test_cases", "source_key")
    for table, column in [
        ("test_items", "pattern_table_id"),
        ("test_factors", "table_id"),
        ("test_patterns", "table_id"),
        ("test_expected_values", "table_id"),
    ]:
        op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_constraint(f"fk_{table}_{column}_table", table, type_="foreignkey")
        op.drop_column(table, column)
    op.drop_table("test_pattern_tables")
