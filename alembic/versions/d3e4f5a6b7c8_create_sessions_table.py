"""create sessions table

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-06-05 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """sessionsテーブルを作成する。"""
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="セッションID: ログインセッションを一意に識別するUUID",
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            nullable=False,
            comment="ユーザーID: セッションを所有するユーザーID",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="開始日時: セッションが開始された日時",
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="最終アクセス日時: セッションが最後に確認された日時",
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="有効期限: アイドルタイムアウトの期限日時",
        ),
        sa.Column(
            "absolute_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="絶対有効期限: 延長できない最大セッション期限日時",
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="失効日時: セッションが失効された日時",
        ),
        sa.Column(
            "revoked_reason",
            sa.String(length=50),
            nullable=True,
            comment="失効理由: logout/expiredなどのセッション失効理由",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="作成日時: レコードが作成された日時",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="更新日時: レコードが最後に更新された日時",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        comment="セッション: ログインセッションを管理するテーブル",
    )
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"])
    op.create_index(op.f("ix_sessions_expires_at"), "sessions", ["expires_at"])
    op.create_index(
        op.f("ix_sessions_absolute_expires_at"),
        "sessions",
        ["absolute_expires_at"],
    )
    op.create_index(op.f("ix_sessions_revoked_at"), "sessions", ["revoked_at"])


def downgrade() -> None:
    """sessionsテーブルを削除する。"""
    op.drop_index(op.f("ix_sessions_revoked_at"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_absolute_expires_at"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_expires_at"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_table("sessions")
