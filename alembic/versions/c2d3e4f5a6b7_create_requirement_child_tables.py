"""create requirement child tables

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-05-21 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_COMMENTS = {
    "requirement_details": "要件詳細: 要件種別ごとの差分項目をJSONで管理するテーブル",
    "requirement_links": "要件リンク: 要件と成果物の紐づけを管理するテーブル",
    "requirement_reviews": "要件レビュー: 要件レビュー結果を管理するテーブル",
    "requirement_comments": "要件コメント: 要件に対するコメントを管理するテーブル",
}

COLUMN_COMMENTS = {
    "requirement_details": {
        "id": "要件詳細ID: 要件詳細を一意に識別するID",
        "requirement_id": "要件ID: 詳細を紐づける要件ID",
        "detail_type": "詳細種別: 詳細JSONの種別",
        "detail_json": "詳細JSON: 要件種別ごとの追加情報JSON",
        "created_at": "作成日時: レコードが作成された日時",
        "updated_at": "更新日時: レコードが最後に更新された日時",
    },
    "requirement_links": {
        "id": "要件リンクID: 要件リンクを一意に識別するID",
        "requirement_id": "要件ID: 紐づけ元の要件ID",
        "linked_type": "リンク種別: screen/api/databaseなどの紐づけ先種別",
        "linked_id": "リンク先ID: 紐づけ先リソースの識別子",
        "created_at": "作成日時: レコードが作成された日時",
    },
    "requirement_reviews": {
        "id": "要件レビューID: 要件レビューを一意に識別するID",
        "requirement_id": "要件ID: レビュー対象の要件ID",
        "reviewer_id": "レビュー担当者ID: レビューを行うユーザーID",
        "status": "レビューステータス: pending/approved/rejected/commentedの状態",
        "comment": "コメント: レビューコメント",
        "reviewed_at": "レビュー日時: レビューが行われた日時",
        "created_at": "作成日時: レコードが作成された日時",
        "updated_at": "更新日時: レコードが最後に更新された日時",
    },
    "requirement_comments": {
        "id": "要件コメントID: 要件コメントを一意に識別するID",
        "requirement_id": "要件ID: コメント対象の要件ID",
        "user_id": "ユーザーID: コメントを書いたユーザーID",
        "comment": "コメント: コメント本文",
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
        "requirement_details",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("detail_type", sa.String(length=100), nullable=False),
        sa.Column("detail_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["requirement_id"], ["requirements.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_requirement_details_id"), "requirement_details", ["id"], unique=False)
    op.create_index(
        op.f("ix_requirement_details_requirement_id"),
        "requirement_details",
        ["requirement_id"],
        unique=False,
    )

    op.create_table(
        "requirement_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("linked_type", sa.String(length=100), nullable=False),
        sa.Column("linked_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["requirement_id"], ["requirements.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_requirement_links_id"), "requirement_links", ["id"], unique=False)
    op.create_index(
        op.f("ix_requirement_links_requirement_id"),
        "requirement_links",
        ["requirement_id"],
        unique=False,
    )

    op.create_table(
        "requirement_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["requirement_id"], ["requirements.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_requirement_reviews_id"), "requirement_reviews", ["id"], unique=False)
    op.create_index(
        op.f("ix_requirement_reviews_requirement_id"),
        "requirement_reviews",
        ["requirement_id"],
        unique=False,
    )

    op.create_table(
        "requirement_comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["requirement_id"], ["requirements.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_requirement_comments_id"), "requirement_comments", ["id"], unique=False)
    op.create_index(
        op.f("ix_requirement_comments_requirement_id"),
        "requirement_comments",
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
    op.drop_index(op.f("ix_requirement_comments_requirement_id"), table_name="requirement_comments")
    op.drop_index(op.f("ix_requirement_comments_id"), table_name="requirement_comments")
    op.drop_table("requirement_comments")
    op.drop_index(op.f("ix_requirement_reviews_requirement_id"), table_name="requirement_reviews")
    op.drop_index(op.f("ix_requirement_reviews_id"), table_name="requirement_reviews")
    op.drop_table("requirement_reviews")
    op.drop_index(op.f("ix_requirement_links_requirement_id"), table_name="requirement_links")
    op.drop_index(op.f("ix_requirement_links_id"), table_name="requirement_links")
    op.drop_table("requirement_links")
    op.drop_index(op.f("ix_requirement_details_requirement_id"), table_name="requirement_details")
    op.drop_index(op.f("ix_requirement_details_id"), table_name="requirement_details")
    op.drop_table("requirement_details")
