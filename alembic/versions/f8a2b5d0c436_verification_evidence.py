"""検証証拠の提出と保全試行台帳を追加する。"""

from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "f8a2b5d0c436"
down_revision = "e7f1a4c9b325"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    bind = op.get_bind()
    # 既存の版固定確認表を残したまま親FKのDDLロックを取りに行かない。
    if bind.scalar(sa.text("SELECT EXISTS(SELECT FROM requirement_verification_plans)")):
        raise RuntimeError("確認表履歴のある環境ではflagで停止し、証拠基盤を保持してください")
    if bind.scalar(sa.text("SELECT EXISTS(SELECT FROM requirement_verification_evidence_sets) OR EXISTS(SELECT FROM requirement_verification_evidence_objects)")):
        raise RuntimeError("提出と保全履歴を保持してください。flagで停止できます")
    if bind.scalar(sa.text("SELECT EXISTS(SELECT FROM role_permissions rp JOIN roles r ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id WHERE p.code='requirement_verification:submit' AND NOT(r.scope='project' AND r.key IN ('project_admin','manager','member')))")):
        raise RuntimeError("独自の権限割当があるため戻せません")
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code='requirement_verification:submit')")
    op.execute("DELETE FROM permissions WHERE code='requirement_verification:submit'")
    op.drop_table("requirement_verification_evidence_objects")
    op.drop_table("requirement_verification_evidence_sets")
    for name in ("guard_verification_evidence_set", "guard_verification_evidence_object", "check_verification_evidence_complete"):
        op.execute(f"DROP FUNCTION {name}()")
