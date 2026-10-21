"""公開準備版の旧形式を保持し、目標を含むv2を許可する。"""

import sqlalchemy as sa

from alembic import op

revision = "d6e0f3b8a214"
down_revision = "c5d9e2a7f103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """封印した旧データを変更せずCHECKだけを拡張する。"""
    op.drop_constraint(
        "ck_artifact_publication_schema", "artifact_publications", type_="check"
    )
    op.create_check_constraint(
        "ck_artifact_publication_schema",
        "artifact_publications",
        "schema_version=1 AND projection_version IN ('public-preview-v1','public-preview-v2')",
    )


def downgrade() -> None:
    """v2の固定版/公開版がある環境は前進修正とする。"""
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT FROM artifact_releases WHERE projection_version='internal-preview-v2') OR EXISTS(SELECT FROM artifact_publications WHERE projection_version='public-preview-v2')"
        )
    ):
        raise RuntimeError("Version 2 artifacts must be retained")
    op.drop_constraint(
        "ck_artifact_publication_schema", "artifact_publications", type_="check"
    )
    op.create_check_constraint(
        "ck_artifact_publication_schema",
        "artifact_publications",
        "schema_version=1 AND projection_version='public-preview-v1'",
    )
