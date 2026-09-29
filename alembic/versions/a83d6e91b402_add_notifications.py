"""Add user notifications.

Revision ID: a83d6e91b402
Revises: 73c08ecae547
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.comments import db_comment

revision = "a83d6e91b402"
down_revision = "73c08ecae547"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """履歴を移行せず、新しいユーザー通知テーブルを作成する。"""
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "recipient_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
        ),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column(
            "event_key",
            sa.String(200),
            nullable=False,
            comment=db_comment("イベントキー", "対象種別・ID・バージョン・通知種別"),
        ),
        sa.Column(
            "snapshot",
            postgresql.JSONB(),
            nullable=False,
            comment=db_comment(
                "表示スナップショット", "操作時の名前・名称・コメント抜粋"
            ),
        ),
        sa.Column(
            "context",
            postgresql.JSONB(),
            nullable=False,
            comment=db_comment(
                "遷移コンテキスト", "要件定義書等の親ID。URLは保存しない"
            ),
        ),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "event_key", "recipient_user_id", name="uq_notification_event_recipient"
        ),
        comment=db_comment("ユーザー通知", "確認する価値のあるユーザー宛てイベント"),
    )
    op.create_index(
        "ix_notifications_recipient_created",
        "notifications",
        ["recipient_user_id", "created_at", "id"],
    )
    op.create_index(
        "ix_notifications_recipient_read_created",
        "notifications",
        ["recipient_user_id", "read_at", "created_at", "id"],
    )
    op.create_index(
        "ix_notifications_recipient_project_created",
        "notifications",
        ["recipient_user_id", "project_id", "created_at", "id"],
    )


def downgrade() -> None:
    """通知テーブルを削除する。"""
    op.drop_table("notifications")
