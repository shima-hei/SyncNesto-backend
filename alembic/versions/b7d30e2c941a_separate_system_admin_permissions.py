"""システム運営Roleから業務Permissionを除く。所属・他Roleは維持する。"""

import sqlalchemy as sa
from alembic import op

revision = "b7d30e2c941a"
down_revision = "a4f92c1e806d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存のsystem/system_adminだけを運営用6権限へ制限する。"""
    op.execute(
        sa.text(
            """
            DELETE FROM role_permissions
            WHERE role_id IN (
                SELECT id FROM roles WHERE key = 'system_admin' AND scope = 'system'
            ) AND permission_id NOT IN (
                SELECT id FROM permissions WHERE code IN (
                    'tenant:manage', 'user:read', 'user:create',
                    'user:update', 'user:mfa_recover', 'user:delete'
                )
            )
            """
        )
    )


def downgrade() -> None:
    """権限縮小は自動で巻き戻さず、旧版へ戻しても業務権限を再付与しない。"""
    pass
