"""Add hashed, single-use account confirmation actions."""

from alembic import op
import sqlalchemy as sa

revision = "d28a41c7b902"
down_revision = "c15a7e92d401"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存Identityを変更せず、本人確認要求を追加する。"""
    op.create_table(
        "account_actions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("requested_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id")),
        sa.Column("purpose", sa.String(50), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("credential_fingerprint", sa.String(64), nullable=False),
        sa.Column("old_email", sa.String(255), nullable=False),
        sa.Column("new_email", sa.String(255)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("provider_message_id", sa.String(255)),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        comment="本人確認要求: 共通Identityのメール承認・再設定",
    )
    op.create_index("ix_account_actions_user_id", "account_actions", ["user_id"])
    op.create_index("ix_account_actions_expires_at", "account_actions", ["expires_at"])


def downgrade() -> None:
    """既存ユーザー・業務データを保持して確認要求だけを取り除く。"""
    op.drop_table("account_actions")
