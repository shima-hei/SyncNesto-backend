"""Store private images attached to comments.

Revision ID: 6e471c52f102
Revises: 3f6d1ab94702
"""

import sqlalchemy as sa

from alembic import op

revision = "6e471c52f102"
down_revision = "3f6d1ab94702"
branch_labels = None
depends_on = None

PARENTS = {
    "task_comment_id": "task_comments",
    "requirement_comment_id": "requirement_target_comments",
    "specification_comment_id": "specification_comments",
    "test_comment_id": "test_design_comments",
}


def upgrade() -> None:
    op.create_table(
        "comment_images",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="画像識別子"),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            comment="保有案件",
        ),
        sa.Column(
            "uploaded_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            comment="送信者",
        ),
        sa.Column("kind", sa.String(20), nullable=False, comment="コメント種別"),
        sa.Column("filename", sa.String(255), nullable=False, comment="画像の表示名"),
        sa.Column(
            "content_type", sa.String(32), nullable=False, comment="検証済みの画像形式"
        ),
        sa.Column("byte_size", sa.Integer(), nullable=False, comment="ファイルサイズ"),
        sa.Column(
            "storage_key",
            sa.String(512),
            nullable=False,
            unique=True,
            comment="非公開ストレージのキー",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="アップロード日時",
        ),
        *(
            sa.Column(
                column,
                sa.Integer(),
                sa.ForeignKey(f"{table}.id", ondelete="CASCADE"),
                comment=label,
            )
            for (column, table), label in zip(
                PARENTS.items(),
                (
                    "タスクコメント",
                    "要件の対象コメント",
                    "画面・API設計コメント",
                    "テスト設計コメント",
                ),
                strict=True,
            )
        ),
        sa.CheckConstraint(
            " + ".join(
                f"(CASE WHEN {column} IS NULL THEN 0 ELSE 1 END)" for column in PARENTS
            )
            + " <= 1",
            name="ck_comment_images_one_owner",
        ),
        sa.CheckConstraint(
            "kind IN ('task','requirement','specification','test_design') AND "
            "(task_comment_id IS NULL OR kind = 'task') AND "
            "(requirement_comment_id IS NULL OR kind = 'requirement') AND "
            "(specification_comment_id IS NULL OR kind = 'specification') AND "
            "(test_comment_id IS NULL OR kind = 'test_design')",
            name="ck_comment_images_owner_kind",
        ),
        comment="コメント画像: 専用の非公開画像と投稿の所属",
    )
    for column in ("project_id", *PARENTS):
        op.create_index(f"ix_comment_images_{column}", "comment_images", [column])


def downgrade() -> None:
    op.drop_table("comment_images")
