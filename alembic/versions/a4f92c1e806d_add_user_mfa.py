"""Identityの追加認証と復旧を追加する。旧認証は確認済みへ補完しない。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "a4f92c1e806d"
down_revision = "91a7d2b8c406"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存業務データと所属を維持して認証状態を追加する。"""
    op.add_column(
        "users",
        sa.Column(
            "mfa_security_version", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "mfa_recovery_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "mfa_user_handle",
            sa.String(64),
            nullable=False,
            server_default=sa.text("gen_random_uuid()::text"),
            comment="WebAuthn本人識別子: メールに依存しない不透明な識別子",
        ),
    )
    for table in ("sessions", "mcp_connections"):
        op.add_column(table, sa.Column("mfa_verified_at", sa.DateTime(timezone=True)))
        op.add_column(table, sa.Column("mfa_security_version", sa.Integer()))
    op.add_column("sessions", sa.Column("mfa_method", sa.String(20)))
    op.add_column(
        "sessions", sa.Column("mfa_reauthenticated_at", sa.DateTime(timezone=True))
    )
    op.create_table(
        "mfa_factors",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("secret", sa.Text()),
        sa.Column("credential_id", sa.Text(), unique=True),
        sa.Column("public_key", sa.Text()),
        sa.Column("sign_count", sa.BigInteger(), nullable=False),
        sa.Column("last_step", sa.BigInteger(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        comment="MFA認証器: 本人のTOTPまたはPasskey",
    )
    op.create_table(
        "mfa_flows",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("security_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        comment="MFA限定状態: 目的と本人に固定した短期要求",
    )
    op.create_table(
        "mfa_recovery_codes",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("code_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        comment="MFA復旧コード: 平文を再表示しない復旧手段",
    )
    op.create_table(
        "mfa_recovery_requests",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("verification_method", sa.String(30), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("security_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        comment="MFA復旧申請: 本人確認後の再登録を監査する",
    )
    for table in (
        "mfa_factors",
        "mfa_flows",
        "mfa_recovery_codes",
        "mfa_recovery_requests",
    ):
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])
    for table in ("mfa_flows", "mfa_recovery_requests"):
        op.create_index(f"ix_{table}_expires_at", table, ["expires_at"])
    op.execute(
        "INSERT INTO permissions (code, description) VALUES ('user:mfa_recover', '本人確認後のMFA復旧支援') ON CONFLICT (code) DO NOTHING"
    )
    op.execute(
        "INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r CROSS JOIN permissions p WHERE r.key = 'system_admin' AND r.scope = 'system' AND p.code = 'user:mfa_recover' ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    """業務データを維持して追加認証だけを削除する。"""
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'user:mfa_recover')"
    )
    op.execute("DELETE FROM permissions WHERE code = 'user:mfa_recover'")
    for table in (
        "mfa_recovery_requests",
        "mfa_recovery_codes",
        "mfa_flows",
        "mfa_factors",
    ):
        op.drop_table(table)
    for table in ("sessions", "mcp_connections"):
        op.drop_column(table, "mfa_security_version")
        op.drop_column(table, "mfa_verified_at")
    op.drop_column("sessions", "mfa_method")
    op.drop_column("sessions", "mfa_reauthenticated_at")
    for column in ("mfa_user_handle", "mfa_recovery_pending", "mfa_security_version"):
        op.drop_column("users", column)
