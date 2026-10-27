"""固定要件の確認表と追記履歴を追加する。"""

from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "e7f1a4c9b325"
down_revision = "d6e0f3b8a214"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT EXISTS(SELECT FROM requirement_verification_plans) OR EXISTS(SELECT FROM requirement_verification_plan_revisions)")):
        raise RuntimeError("確認表と保存履歴は保持が必要です。flagを無効にしてください")
    if bind.scalar(sa.text("SELECT EXISTS(SELECT FROM role_permissions rp JOIN roles r ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id WHERE p.code IN ('requirement_verification:read','requirement_verification:prepare') AND NOT (r.scope='project' AND ((p.code='requirement_verification:read' AND r.key IN ('project_admin','manager','member','viewer')) OR (p.code='requirement_verification:prepare' AND r.key IN ('project_admin','manager')))))")):
        raise RuntimeError("独自の権限割当があるため確認表のmigrationは戻せません")
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('requirement_verification:read','requirement_verification:prepare'))")
    op.execute("DELETE FROM permissions WHERE code IN ('requirement_verification:read','requirement_verification:prepare')")
    op.drop_constraint("fk_verification_current", "requirement_verification_plans", type_="foreignkey")
    op.drop_table("requirement_verification_plan_revisions")
    op.drop_table("requirement_verification_plans")
    for name in ("guard_requirement_verification_plan", "guard_requirement_verification_revision", "check_requirement_verification_complete"):
        op.execute(f"DROP FUNCTION {name}()")
