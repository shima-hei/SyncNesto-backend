"""create requirement target comments

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-06-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.comments import db_comment

# revision identifiers, used by Alembic.
revision: str = "c8d9e0f1a2b3"
down_revision: str | Sequence[str] | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """requirement_target_commentsテーブルを作成する。"""
    op.create_table(
        "requirement_target_comments",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("コメントID", "コメントを一意に識別するID"),
        ),
        sa.Column(
            "document_id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("要件定義書ID", "コメント対象が属する要件定義書ID"),
        ),
        sa.Column(
            "target_type",
            sa.String(length=100),
            nullable=False,
            comment=db_comment("対象種別", "コメント対象の種別"),
        ),
        sa.Column(
            "target_id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("対象ID", "コメント対象のID"),
        ),
        sa.Column(
            "parent_comment_id",
            sa.Integer(),
            nullable=True,
            comment=db_comment("親コメントID", "返信先のコメントID"),
        ),
        sa.Column(
            "body",
            sa.Text(),
            nullable=False,
            comment=db_comment("本文", "コメント本文"),
        ),
        sa.Column(
            "author_id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("投稿者ID", "コメントを投稿したユーザーID"),
        ),
        sa.Column(
            "is_resolved",
            sa.Boolean(),
            nullable=False,
            comment=db_comment("解決済みフラグ", "コメントが解決済みかを示すフラグ"),
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            comment=db_comment("バージョン", "楽観的排他制御に使用するバージョン番号"),
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
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["requirement_documents.id"]),
        sa.ForeignKeyConstraint(
            ["parent_comment_id"],
            ["requirement_target_comments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        comment=db_comment(
            "要件定義対象コメント",
            "要件定義内の文書・セクション・要件・未決事項へのコメントを管理するテーブル",
        ),
    )
    op.create_index(
        op.f("ix_requirement_target_comments_document_id"),
        "requirement_target_comments",
        ["document_id"],
    )
    op.create_index(
        op.f("ix_requirement_target_comments_id"),
        "requirement_target_comments",
        ["id"],
    )
    op.create_index(
        op.f("ix_requirement_target_comments_target_id"),
        "requirement_target_comments",
        ["target_id"],
    )
    op.create_index(
        op.f("ix_requirement_target_comments_target_type"),
        "requirement_target_comments",
        ["target_type"],
    )


def downgrade() -> None:
    """requirement_target_commentsテーブルを削除する。"""
    op.drop_index(
        op.f("ix_requirement_target_comments_target_type"),
        table_name="requirement_target_comments",
    )
    op.drop_index(
        op.f("ix_requirement_target_comments_target_id"),
        table_name="requirement_target_comments",
    )
    op.drop_index(
        op.f("ix_requirement_target_comments_id"),
        table_name="requirement_target_comments",
    )
    op.drop_index(
        op.f("ix_requirement_target_comments_document_id"),
        table_name="requirement_target_comments",
    )
    op.drop_table("requirement_target_comments")
