"""Project限定の招待・短期確認・追記履歴を追加する。"""

from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "f2a6b9d4c870"
down_revision = "e1f5a8c3b769"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存の参加・承認・権限を変えず、新3表だけを作る。"""
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade() -> None:
    """履歴があるDBの巻戻しは許可しない。"""
    db = op.get_bind()
    db.execute(
        sa.text(
            "LOCK TABLE project_guest_invitations,project_guest_invitation_checks,project_guest_invitation_events IN ACCESS EXCLUSIVE MODE"
        )
    )
    if db.scalar(sa.text("SELECT EXISTS(SELECT FROM project_guest_invitations)")):
        raise RuntimeError(
            "invitation history must be retained; disable new operations instead"
        )
    for name in (
        "project_guest_invitation_events",
        "project_guest_invitation_checks",
        "project_guest_invitations",
    ):
        op.drop_table(name)
    for name in (
        "guard_guest_invitation_event",
        "guard_guest_invitation_check",
        "guard_guest_invitation",
    ):
        op.execute(f"DROP FUNCTION {name}()")
