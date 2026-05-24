"""create requirement tables

Revision ID: b1c2d3e4f5a6
Revises: 0a1b2c3d4e5f
Create Date: 2026-05-21 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "0a1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_COMMENTS = {
    "requirement_documents": "要件定義書: 要件定義書全体を管理するテーブル",
    "requirements": "要件: 要件定義を構成する個別要件を管理するテーブル",
    "requirement_revisions": "要件改訂履歴: 要件の変更履歴を管理するテーブル",
}

COLUMN_COMMENTS = {
    "requirement_documents": {
        "id": "要件定義書ID: 要件定義書を一意に識別するID",
        "project_id": "プロジェクトID: 要件定義書が属するプロジェクトID",
        "title": "タイトル: 要件定義書のタイトル",
        "document_code": "文書コード: プロジェクト内で一意な要件定義書コード",
        "version": "バージョン: 楽観的排他制御に使用するバージョン番号",
        "status": "ステータス: 要件定義書の状態",
        "purpose": "目的: 要件定義書の目的",
        "target_system_name": "対象システム名: 要件定義の対象システム名",
        "client_name": "クライアント名: 発注者または利用部門名",
        "vendor_name": "ベンダー名: 開発または提供ベンダー名",
        "author_id": "作成担当者ID: 要件定義書の作成担当ユーザーID",
        "reviewer_id": "レビュー担当者ID: 要件定義書のレビュー担当ユーザーID",
        "approver_id": "承認者ID: 要件定義書の承認ユーザーID",
        "approved_at": "承認日時: 要件定義書が承認された日時",
        "created_by": "作成者ID: この要件定義書を作成したユーザーID",
        "updated_by": "更新者ID: この要件定義書を最後に更新したユーザーID",
        "created_at": "作成日時: レコードが作成された日時",
        "updated_at": "更新日時: レコードが最後に更新された日時",
        "deleted_at": "削除日時: 論理削除された日時",
    },
    "requirements": {
        "id": "要件ID: 要件を一意に識別するID",
        "document_id": "要件定義書ID: 所属する要件定義書ID",
        "requirement_code": "要件コード: 要件定義書内で一意な要件識別子",
        "requirement_type": "要件種別: business/functionalなどの要件種別",
        "category": "カテゴリ: 要件の分類",
        "title": "タイトル: 要件のタイトル",
        "description": "説明: 要件の本文または説明",
        "rationale": "根拠: 要件が必要な理由や背景",
        "acceptance_criteria": "受入条件: 要件を満たしたと判断する条件",
        "priority": "優先度: must/should/could/won'tの優先度",
        "status": "ステータス: 要件の状態",
        "source": "発生元: 要件の発生元や参照元",
        "owner_id": "担当者ID: 要件の担当ユーザーID",
        "approved_by": "承認者ID: 要件を承認したユーザーID",
        "approved_at": "承認日時: 要件が承認された日時",
        "version": "バージョン: 楽観的排他制御に使用するバージョン番号",
        "created_by": "作成者ID: この要件を作成したユーザーID",
        "updated_by": "更新者ID: この要件を最後に更新したユーザーID",
        "created_at": "作成日時: レコードが作成された日時",
        "updated_at": "更新日時: レコードが最後に更新された日時",
        "deleted_at": "削除日時: 論理削除された日時",
    },
    "requirement_revisions": {
        "id": "要件改訂履歴ID: 要件改訂履歴を一意に識別するID",
        "requirement_id": "要件ID: 変更対象の要件ID",
        "version": "バージョン: 変更後の要件バージョン",
        "changed_by": "変更者ID: 要件を変更したユーザーID",
        "change_summary": "変更概要: 変更内容の概要",
        "before_value": "変更前値: 変更前の要件情報JSON",
        "after_value": "変更後値: 変更後の要件情報JSON",
        "reason": "変更理由: 変更した理由",
        "created_at": "作成日時: レコードが作成された日時",
    },
}


def _escape_comment(comment: str) -> str:
    """SQLコメント用にシングルクォートをエスケープする。"""
    return comment.replace("'", "''")


def _set_table_comment(table_name: str, comment: str | None) -> None:
    """テーブルコメントを設定する。"""
    comment_sql = "NULL" if comment is None else f"'{_escape_comment(comment)}'"
    op.execute(f'COMMENT ON TABLE "{table_name}" IS {comment_sql}')


def _set_column_comment(table_name: str, column_name: str, comment: str | None) -> None:
    """カラムコメントを設定する。"""
    comment_sql = "NULL" if comment is None else f"'{_escape_comment(comment)}'"
    op.execute(f'COMMENT ON COLUMN "{table_name}"."{column_name}" IS {comment_sql}')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "requirement_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("document_code", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("target_system_name", sa.String(length=255), nullable=True),
        sa.Column("client_name", sa.String(length=255), nullable=True),
        sa.Column("vendor_name", sa.String(length=255), nullable=True),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
        sa.Column("approver_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["approver_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "document_code",
            name="uq_requirement_documents_project_document_code",
        ),
    )
    op.create_index(
        op.f("ix_requirement_documents_id"),
        "requirement_documents",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_requirement_documents_project_id"),
        "requirement_documents",
        ["project_id"],
        unique=False,
    )

    op.create_table(
        "requirements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("requirement_code", sa.String(length=100), nullable=False),
        sa.Column("requirement_type", sa.String(length=50), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("acceptance_criteria", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["requirement_documents.id"]),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "requirement_code",
            name="uq_requirements_document_requirement_code",
        ),
    )
    op.create_index(op.f("ix_requirements_document_id"), "requirements", ["document_id"], unique=False)
    op.create_index(op.f("ix_requirements_id"), "requirements", ["id"], unique=False)

    op.create_table(
        "requirement_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changed_by", sa.Integer(), nullable=True),
        sa.Column("change_summary", sa.String(length=255), nullable=True),
        sa.Column("before_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["requirement_id"], ["requirements.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_requirement_revisions_id"),
        "requirement_revisions",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_requirement_revisions_requirement_id"),
        "requirement_revisions",
        ["requirement_id"],
        unique=False,
    )

    for table_name, comment in TABLE_COMMENTS.items():
        _set_table_comment(table_name, comment)
    for table_name, column_comments in COLUMN_COMMENTS.items():
        for column_name, comment in column_comments.items():
            _set_column_comment(table_name, column_name, comment)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_requirement_revisions_requirement_id"), table_name="requirement_revisions")
    op.drop_index(op.f("ix_requirement_revisions_id"), table_name="requirement_revisions")
    op.drop_table("requirement_revisions")
    op.drop_index(op.f("ix_requirements_id"), table_name="requirements")
    op.drop_index(op.f("ix_requirements_document_id"), table_name="requirements")
    op.drop_table("requirements")
    op.drop_index(op.f("ix_requirement_documents_project_id"), table_name="requirement_documents")
    op.drop_index(op.f("ix_requirement_documents_id"), table_name="requirement_documents")
    op.drop_table("requirement_documents")
