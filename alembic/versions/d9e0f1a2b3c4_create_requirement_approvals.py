"""create requirement approvals

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-06-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.comments import db_comment

# revision identifiers, used by Alembic.
revision: str = "d9e0f1a2b3c4"
down_revision: str | Sequence[str] | None = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """requirement_approvalsテーブルを作成する。"""
    op.create_table(
        "requirement_approvals",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("承認ID", "承認を一意に識別するID"),
        ),
        sa.Column(
            "document_id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("要件定義書ID", "承認対象が属する要件定義書ID"),
        ),
        sa.Column(
            "target_type",
            sa.String(length=100),
            nullable=False,
            comment=db_comment("対象種別", "承認対象の種別"),
        ),
        sa.Column(
            "target_id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("対象ID", "承認対象のID"),
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            comment=db_comment("ステータス", "requested/approved/rejectedの状態"),
        ),
        sa.Column(
            "approver_id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("承認者ID", "承認判断を行うユーザーID"),
        ),
        sa.Column(
            "requested_by",
            sa.Integer(),
            nullable=False,
            comment=db_comment("申請者ID", "承認申請を行ったユーザーID"),
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment=db_comment("申請日時", "承認申請が行われた日時"),
        ),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=db_comment("承認日時", "承認された日時"),
        ),
        sa.Column(
            "rejected_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=db_comment("差し戻し日時", "差し戻しされた日時"),
        ),
        sa.Column(
            "comment",
            sa.Text(),
            nullable=True,
            comment=db_comment("コメント", "承認申請または判断時のコメント"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment=db_comment("作成日時", "レコードが作成された日時"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment=db_comment("更新日時", "レコードが最後に更新された日時"),
        ),
        sa.ForeignKeyConstraint(["approver_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["requirement_documents.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        comment=db_comment(
            "要件定義承認",
            "要件定義対象の承認申請と承認結果を管理するテーブル",
        ),
    )
    op.create_index(
        op.f("ix_requirement_approvals_id"),
        "requirement_approvals",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_requirement_approvals_document_target",
        "requirement_approvals",
        ["document_id", "target_type", "target_id"],
        unique=False,
    )


def downgrade() -> None:
    """requirement_approvalsテーブルを削除する。"""
    op.drop_index(
        "ix_requirement_approvals_document_target",
        table_name="requirement_approvals",
    )
    op.drop_index(
        op.f("ix_requirement_approvals_id"),
        table_name="requirement_approvals",
    )
    op.drop_table("requirement_approvals")
