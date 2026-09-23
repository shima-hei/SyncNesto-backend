"""組み合わせに複数の期待値を設定できるようにする。"""

from alembic import op
import re
import sqlalchemy as sa

revision = "9e0f1a2b3c4d"
down_revision = "8d9e0f1a2b3c"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    for constraint in sa.inspect(connection).get_unique_constraints(
        "test_pattern_expected_values"
    ):
        if set(constraint["column_names"]) == {"design_id", "pattern_id"}:
            name = constraint["name"]
            assert name is not None
            op.drop_constraint(name, "test_pattern_expected_values", type_="unique")
    op.create_unique_constraint(
        "uq_pattern_expected_selection",
        "test_pattern_expected_values",
        ["design_id", "pattern_id", "expected_value_id"],
    )
    rows = (
        connection.execute(
            sa.text(
                "SELECT id, design_id, table_id, code FROM test_patterns ORDER BY position, id"
            )
        )
        .mappings()
        .all()
    )
    used = {}
    for row in rows:
        used.setdefault((row["design_id"], row["table_id"]), set()).add(row["code"])
    changed = set()
    for row in rows:
        match = re.fullmatch(r"組み合わせ(\d+)", row["code"])
        if not match:
            continue
        codes = used[(row["design_id"], row["table_id"])]
        number = int(match[1])
        while f"P{number:03d}" in codes:
            number += 1
        code = f"P{number:03d}"
        codes.add(code)
        connection.execute(
            sa.text("UPDATE test_patterns SET code=:code WHERE id=:id"),
            {"code": code, "id": row["id"]},
        )
        changed.add(row["design_id"])
    for design_id in changed:
        connection.execute(
            sa.text("UPDATE test_designs SET version=version+1 WHERE id=:id"),
            {"id": design_id},
        )


def downgrade():
    # 複数選択が残っていれば制約追加が失敗する。データは削除しない。
    op.create_unique_constraint(
        "test_pattern_expected_values_design_id_pattern_id_key",
        "test_pattern_expected_values",
        ["design_id", "pattern_id"],
    )
    op.drop_constraint(
        "uq_pattern_expected_selection", "test_pattern_expected_values", type_="unique"
    )
