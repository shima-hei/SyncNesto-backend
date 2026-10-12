"""OpenAIのOAuth relay stateに対応して保存上限を広げる。"""

import sqlalchemy as sa
from alembic import op

revision = "d4f8b1e6a902"
down_revision = "c2a7e491d6b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存値・所有者・GRANTを変えずにvarcharの上限だけを広げる。"""
    op.alter_column(
        "mcp_authorization_requests",
        "state",
        existing_type=sa.String(length=500),
        type_=sa.String(length=2048),
        existing_nullable=False,
    )


def downgrade() -> None:
    """元の上限へ戻す。長い値が残る場合はDBが拒否し、切り詰めない。"""
    op.alter_column(
        "mcp_authorization_requests",
        "state",
        existing_type=sa.String(length=2048),
        type_=sa.String(length=500),
        existing_nullable=False,
    )
