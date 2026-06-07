"""create audit logs table

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-06-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.models.comments import db_comment

# revision identifiers, used by Alembic.
revision: str = "f5a6b7c8d9e0"
down_revision: str | Sequence[str] | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """audit_logsテーブルを作成する。"""
    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("監査ログID", "監査ログを一意に識別するID"),
        ),
        sa.Column(
            "event_type",
            sa.String(length=100),
            nullable=False,
            comment=db_comment("イベント種別", "監査対象操作を表すイベント種別"),
        ),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            nullable=True,
            comment=db_comment("操作ユーザーID", "操作を実行したユーザーID"),
        ),
        sa.Column(
            "target_user_id",
            sa.Integer(),
            nullable=True,
            comment=db_comment("対象ユーザーID", "操作対象になったユーザーID"),
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            nullable=True,
            comment=db_comment("プロジェクトID", "関連するプロジェクトID"),
        ),
        sa.Column(
            "resource_type",
            sa.String(length=100),
            nullable=True,
            comment=db_comment("リソース種別", "操作対象リソースの種別"),
        ),
        sa.Column(
            "resource_id",
            sa.Integer(),
            nullable=True,
            comment=db_comment("リソースID", "操作対象リソースのID"),
        ),
        sa.Column(
            "ip_address",
            sa.String(length=100),
            nullable=True,
            comment=db_comment("IPアドレス", "接続元IPアドレス"),
        ),
        sa.Column(
            "user_agent",
            sa.String(length=1000),
            nullable=True,
            comment=db_comment("User-Agent", "リクエストのUser-Agent"),
        ),
        sa.Column(
            "request_id",
            sa.String(length=100),
            nullable=True,
            comment=db_comment("リクエストID", "リクエストを識別するID"),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment=db_comment("メタデータ", "操作補足情報。秘匿情報は保存しない"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment=db_comment("作成日時", "監査ログが作成された日時"),
        ),
        sa.PrimaryKeyConstraint("id"),
        comment=db_comment("監査ログ", "重要操作の監査証跡を管理するテーブル"),
    )
    op.create_index(
        op.f("ix_audit_logs_actor_user_id"),
        "audit_logs",
        ["actor_user_id"],
    )
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"])
    op.create_index(op.f("ix_audit_logs_event_type"), "audit_logs", ["event_type"])
    op.create_index(op.f("ix_audit_logs_project_id"), "audit_logs", ["project_id"])
    op.create_index(op.f("ix_audit_logs_request_id"), "audit_logs", ["request_id"])
    op.create_index(op.f("ix_audit_logs_resource_id"), "audit_logs", ["resource_id"])
    op.create_index(
        op.f("ix_audit_logs_resource_type"),
        "audit_logs",
        ["resource_type"],
    )
    op.create_index(
        op.f("ix_audit_logs_target_user_id"),
        "audit_logs",
        ["target_user_id"],
    )


def downgrade() -> None:
    """audit_logsテーブルを削除する。"""
    op.drop_index(op.f("ix_audit_logs_target_user_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_resource_type"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_resource_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_request_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_project_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_event_type"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_actor_user_id"), table_name="audit_logs")
    op.drop_table("audit_logs")
