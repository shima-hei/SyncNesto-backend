"""Add opt-in approval mail records, without backfill or permission changes."""

from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "b4c8d1f6e092"
down_revision = "a3b7c0e5d981"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """通知の正本を保持し、空の配送台帳だけを追加する。"""
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade() -> None:
    """配送履歴がある場合は破棄せず停止する。"""
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT FROM notification_email_deliveries) OR EXISTS(SELECT FROM notification_email_preferences)"
        )
    ):
        raise RuntimeError("Mail history must be retained; disable the feature instead")
    for name in (
        "notification_email_attempts",
        "notification_email_deliveries",
        "notification_email_preferences",
    ):
        op.drop_table(name)
