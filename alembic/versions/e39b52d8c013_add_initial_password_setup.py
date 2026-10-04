"""Add initial password setup without restricting existing accounts."""

import sqlalchemy as sa

from alembic import op

revision = "e39b52d8c013"
down_revision = "d28a41c7b902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存Identityは設定済みとして保持し、新規発行用の状態を追加する。"""
    op.add_column(
        "users",
        sa.Column(
            "password_change_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "initial_password_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    """初回設定状態だけを削除する。"""
    op.drop_column("users", "initial_password_expires_at")
    op.drop_column("users", "password_change_required")
