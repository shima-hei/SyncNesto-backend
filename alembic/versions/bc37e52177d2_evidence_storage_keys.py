"""旧証拠を保持し、新しい追記保存のキーを許可する。"""

import sqlalchemy as sa

from alembic import op

revision = "bc37e52177d2"
down_revision = "ab26d41066c1"
branch_labels = None
depends_on = None

TABLE = "requirement_verification_evidence_objects"
LEGACY_CHECK = "requirement_verification_evidence_objects_storage_key_check"
CURRENT_CHECK = "ck_verification_evidence_storage_key"


def upgrade():
    """旧行を変換せず、予約キーの許可範囲だけを拡張する。"""
    op.create_check_constraint(
        CURRENT_CHECK,
        TABLE,
        "storage_key ~ '^verification-evidence-v[12]/[a-f0-9]{32}$'",
    )
    op.drop_constraint(LEGACY_CHECK, TABLE, type_="check")


def downgrade():
    """新方式の証拠がある環境で、読めない版への移行を防ぐ。"""
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT FROM requirement_verification_evidence_objects "
            "WHERE storage_key LIKE 'verification-evidence-v2/%')"
        )
    ):
        raise RuntimeError("新方式の証拠と互換readerを保持してください")
    op.create_check_constraint(
        LEGACY_CHECK, TABLE, "storage_key ~ '^verification-evidence-v1/[a-f0-9]{32}$'"
    )
    op.drop_constraint(CURRENT_CHECK, TABLE, type_="check")
