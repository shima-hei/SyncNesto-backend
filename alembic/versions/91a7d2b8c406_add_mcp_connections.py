"""MCPの本人委任・資格情報・再試行結果を追加する。

Revision ID: 91a7d2b8c406
Revises: 48bb3c9773b3
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "91a7d2b8c406"
down_revision = "48bb3c9773b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """通常のログイン・デモ用テーブルは変更せず連携用テーブルを追加する。"""
    op.create_table(
        "mcp_connections",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("client_id", sa.String(100), nullable=False),
        sa.Column("resource", sa.String(500), nullable=False),
        sa.Column("project_ids", pg.JSONB(), nullable=False),
        sa.Column("scopes", pg.JSONB(), nullable=False),
        sa.Column("credential_fingerprint", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        comment="MCP接続: 本人が許可した業務操作の範囲",
    )
    op.create_index("ix_mcp_connections_user_id", "mcp_connections", ["user_id"])
    op.create_index("ix_mcp_connections_tenant_id", "mcp_connections", ["tenant_id"])
    op.create_table(
        "mcp_authorization_requests",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", sa.String(100), nullable=False),
        sa.Column("redirect_uri", sa.String(500), nullable=False),
        sa.Column("resource", sa.String(500), nullable=False),
        sa.Column("scopes", pg.JSONB(), nullable=False),
        sa.Column("state", sa.String(500), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("code_hash", sa.String(64), unique=True),
        sa.Column(
            "connection_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("mcp_connections.id", ondelete="CASCADE"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        comment="MCP認可要求: 一回限りの認可コードとPKCE",
    )
    op.create_table(
        "mcp_credentials",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("mcp_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        comment="MCP資格情報: 用途・期限・使用済み状態を検証するハッシュ",
    )
    op.create_index(
        "ix_mcp_credentials_connection_id", "mcp_credentials", ["connection_id"]
    )
    op.create_table(
        "mcp_operation_receipts",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("mcp_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operation_key", sa.String(100), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result", pg.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "connection_id", "operation_key", name="uq_mcp_operation_key"
        ),
        comment="MCP操作結果: 重複作成を防ぐ本人接続ごとの記録",
    )
    op.create_index(
        "ix_mcp_operation_receipts_connection_id",
        "mcp_operation_receipts",
        ["connection_id"],
    )
    op.execute(
        "INSERT INTO permissions (code, description) VALUES ('mcp:connect', '本人の権限でMCPを利用する') ON CONFLICT (code) DO NOTHING"
    )
    op.execute("""INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.scope = 'project' AND r.key IN ('project_admin', 'manager', 'member')
        AND p.code = 'mcp:connect' ON CONFLICT DO NOTHING""")


def downgrade() -> None:
    """連携用データと追加permissionだけを削除する。"""
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code='mcp:connect')"
    )
    op.execute("DELETE FROM permissions WHERE code='mcp:connect'")
    for name in (
        "mcp_operation_receipts",
        "mcp_credentials",
        "mcp_authorization_requests",
        "mcp_connections",
    ):
        op.drop_table(name)
