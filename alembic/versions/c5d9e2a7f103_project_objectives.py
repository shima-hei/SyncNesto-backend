"""文書に目標正本、案件に参照、追記専用の選択履歴を追加する。"""

from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "c5d9e2a7f103"
down_revision = "b4c8d1f6e092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """旧値を転記せず、同案件の参照と専用権限を追加する。"""
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade() -> None:
    """新本文や選択履歴を失うdowngradeを拒否する。"""
    if op.get_bind().scalar(
        sa.text("""
        SELECT EXISTS(SELECT FROM project_goal_source_events)
        OR EXISTS(SELECT FROM projects WHERE goal_document_id IS NOT NULL OR goal_source_version<>1)
        OR EXISTS(SELECT FROM requirement_documents WHERE project_goal IS NOT NULL OR project_scope IS NOT NULL OR project_completion_criteria IS NOT NULL)
        OR EXISTS(SELECT FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id JOIN roles r ON r.id=rp.role_id
          WHERE p.code='project:manage_goal_source' AND (r.scope<>'project' OR r.key NOT IN ('project_admin','manager')))
    """)
    ):
        raise RuntimeError(
            "Objective data/history must be retained; disable the feature instead"
        )
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code='project:manage_goal_source')"
    )
    op.execute("DELETE FROM permissions WHERE code='project:manage_goal_source'")
    op.drop_table("project_goal_source_events")
    op.execute("DROP FUNCTION protect_goal_source_event()")
    op.drop_constraint("fk_project_goal_document", "projects", type_="foreignkey")
    op.drop_constraint("ck_project_goal_version", "projects", type_="check")
    op.drop_column("projects", "goal_document_id")
    op.drop_column("projects", "goal_source_version")
    for name in (
        "ck_document_goal_length",
        "ck_document_scope_length",
        "ck_document_completion_length",
    ):
        op.drop_constraint(name, "requirement_documents", type_="check")
    op.drop_constraint(
        "uq_requirement_document_identity", "requirement_documents", type_="unique"
    )
    for name in ("project_goal", "project_scope", "project_completion_criteria"):
        op.drop_column("requirement_documents", name)
