"""固定要件の指定本人による判定と追記履歴を追加する。"""

from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "f9b3c6e1d547"
down_revision = "f8a2b5d0c436"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT EXISTS(SELECT FROM requirement_verification_plans) OR EXISTS(SELECT FROM requirement_verification_requests)")):
        raise RuntimeError("確認表・判定履歴を保持し、flagで停止してください")
    if bind.scalar(sa.text("SELECT EXISTS(SELECT FROM role_permissions rp JOIN roles r ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id WHERE p.code='requirement_verification:decide' AND NOT(r.scope='project' AND r.key IN ('project_admin','manager','member')))")):
        raise RuntimeError("独自の権限割当があるため戻せません")
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code='requirement_verification:decide')")
    op.execute("DELETE FROM permissions WHERE code='requirement_verification:decide'")
    op.drop_table("requirement_verification_events")
    op.drop_table("requirement_verification_requests")
    for name in ("guard_verification_request", "guard_verification_event", "check_verification_request_complete"):
        op.execute(f"DROP FUNCTION {name}()")
