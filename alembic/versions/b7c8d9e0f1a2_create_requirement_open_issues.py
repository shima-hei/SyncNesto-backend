"""create requirement open issues and change logs

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-06-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.models.comments import db_comment

# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: str | Sequence[str] | None = "a6b7c8d9e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """未決事項テーブルと要件定義変更履歴テーブルを作成する。"""
    op.create_table(
        "requirement_open_issues",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("未決事項ID", "未決事項を一意に識別するID"),
        ),
        sa.Column(
            "document_id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("要件定義書ID", "所属する要件定義書ID"),
        ),
        sa.Column(
            "related_requirement_id",
            sa.Integer(),
            nullable=True,
            comment=db_comment("関連要件ID", "関連または昇格先の要件ID"),
        ),
        sa.Column(
            "issue_code",
            sa.String(length=100),
            nullable=False,
            comment=db_comment("未決事項コード", "要件定義書内で一意な未決事項コード"),
        ),
        sa.Column(
            "title",
            sa.String(length=255),
            nullable=False,
            comment=db_comment("タイトル", "未決事項のタイトル"),
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
            comment=db_comment("説明", "未決事項の本文または説明"),
        ),
        sa.Column(
            "impact_scope",
            sa.Text(),
            nullable=True,
            comment=db_comment("影響範囲", "未決事項が影響する範囲"),
        ),
        sa.Column(
            "assignee_id",
            sa.Integer(),
            nullable=True,
            comment=db_comment("担当者ID", "未決事項の担当ユーザーID"),
        ),
        sa.Column(
            "due_date",
            sa.Date(),
            nullable=True,
            comment=db_comment("期限日", "未決事項の対応期限日"),
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            comment=db_comment("ステータス", "未決事項の状態"),
        ),
        sa.Column(
            "resolution",
            sa.Text(),
            nullable=True,
            comment=db_comment("解決内容", "未決事項の解決内容"),
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            comment=db_comment("バージョン", "楽観的排他制御に使用するバージョン番号"),
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            nullable=True,
            comment=db_comment("作成者ID", "この未決事項を作成したユーザーID"),
        ),
        sa.Column(
            "updated_by",
            sa.Integer(),
            nullable=True,
            comment=db_comment("更新者ID", "この未決事項を最後に更新したユーザーID"),
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
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=db_comment("削除日時", "論理削除された日時"),
        ),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["requirement_documents.id"]),
        sa.ForeignKeyConstraint(["related_requirement_id"], ["requirements.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "issue_code",
            name="uq_requirement_open_issues_document_issue_code",
        ),
        comment=db_comment(
            "要件定義未決事項",
            "要件定義書内の未決事項を管理するテーブル",
        ),
    )
    op.create_index(
        op.f("ix_requirement_open_issues_document_id"),
        "requirement_open_issues",
        ["document_id"],
    )
    op.create_index(
        op.f("ix_requirement_open_issues_id"),
        "requirement_open_issues",
        ["id"],
    )

    op.create_table(
        "requirement_change_logs",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("要件定義変更履歴ID", "変更履歴を一意に識別するID"),
        ),
        sa.Column(
            "document_id",
            sa.Integer(),
            nullable=True,
            comment=db_comment("要件定義書ID", "関連する要件定義書ID"),
        ),
        sa.Column(
            "target_type",
            sa.String(length=100),
            nullable=False,
            comment=db_comment("対象種別", "変更対象の種別"),
        ),
        sa.Column(
            "target_id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("対象ID", "変更対象のID"),
        ),
        sa.Column(
            "action",
            sa.String(length=100),
            nullable=False,
            comment=db_comment("操作", "created/updated/deletedなどの操作種別"),
        ),
        sa.Column(
            "field_name",
            sa.String(length=100),
            nullable=True,
            comment=db_comment("項目名", "変更された項目名"),
        ),
        sa.Column(
            "old_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=db_comment("変更前値", "変更前の値JSON"),
        ),
        sa.Column(
            "new_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=db_comment("変更後値", "変更後の値JSON"),
        ),
        sa.Column(
            "reason",
            sa.Text(),
            nullable=True,
            comment=db_comment("変更理由", "変更した理由"),
        ),
        sa.Column(
            "changed_by",
            sa.Integer(),
            nullable=True,
            comment=db_comment("変更者ID", "変更したユーザーID"),
        ),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment=db_comment("変更日時", "変更が記録された日時"),
        ),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["requirement_documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        comment=db_comment(
            "要件定義変更履歴",
            "要件定義に関する横断的な変更履歴を管理するテーブル",
        ),
    )
    op.create_index(
        op.f("ix_requirement_change_logs_action"),
        "requirement_change_logs",
        ["action"],
    )
    op.create_index(
        op.f("ix_requirement_change_logs_document_id"),
        "requirement_change_logs",
        ["document_id"],
    )
    op.create_index(
        op.f("ix_requirement_change_logs_id"),
        "requirement_change_logs",
        ["id"],
    )
    op.create_index(
        op.f("ix_requirement_change_logs_target_id"),
        "requirement_change_logs",
        ["target_id"],
    )
    op.create_index(
        op.f("ix_requirement_change_logs_target_type"),
        "requirement_change_logs",
        ["target_type"],
    )


def downgrade() -> None:
    """未決事項テーブルと要件定義変更履歴テーブルを削除する。"""
    op.drop_index(
        op.f("ix_requirement_change_logs_target_type"),
        table_name="requirement_change_logs",
    )
    op.drop_index(
        op.f("ix_requirement_change_logs_target_id"),
        table_name="requirement_change_logs",
    )
    op.drop_index(
        op.f("ix_requirement_change_logs_id"),
        table_name="requirement_change_logs",
    )
    op.drop_index(
        op.f("ix_requirement_change_logs_document_id"),
        table_name="requirement_change_logs",
    )
    op.drop_index(
        op.f("ix_requirement_change_logs_action"),
        table_name="requirement_change_logs",
    )
    op.drop_table("requirement_change_logs")
    op.drop_index(
        op.f("ix_requirement_open_issues_id"),
        table_name="requirement_open_issues",
    )
    op.drop_index(
        op.f("ix_requirement_open_issues_document_id"),
        table_name="requirement_open_issues",
    )
    op.drop_table("requirement_open_issues")
