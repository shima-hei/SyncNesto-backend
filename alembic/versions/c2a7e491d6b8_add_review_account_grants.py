"""期限付き審査アカウントの許可を追加する。既存Identityは変更しない。"""

import sqlalchemy as sa
from alembic import op

revision = "c2a7e491d6b8"
down_revision = "b7d30e2c941a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """空の許可テーブルだけを作成し、MFA免除は自動付与しない。"""
    op.create_table(
        "review_account_grants",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("tenants.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        comment="審査用利用許可: 通常MFA方針と分離した期限付き審査Identity",
    )
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'syncnesto_app') THEN
            REVOKE ALL PRIVILEGES ON TABLE review_account_grants FROM syncnesto_app;
            GRANT SELECT ON TABLE review_account_grants TO syncnesto_app;
        END IF;
        END $$"""
    )


def downgrade() -> None:
    """許可テーブルを除く。残るIdentityには通常MFAが適用される。"""
    op.drop_table("review_account_grants")
