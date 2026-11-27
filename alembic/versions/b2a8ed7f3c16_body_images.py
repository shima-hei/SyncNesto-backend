"""本文画像を既存の非公開画像テーブルに追加する。

Revision ID: b2a8ed7f3c16
Revises: 6e471c52f102
"""

import sqlalchemy as sa

from alembic import op

revision = "b2a8ed7f3c16"
down_revision = "6e471c52f102"
branch_labels = None
depends_on = None

COMMENT_KINDS = "'task','requirement','specification','test_design'"
BODY_KINDS = (
    "'body_task','body_requirement','body_specification',"
    "'body_test_design','body_document'"
)
OWNER_KIND = "ck_comment_images_owner_kind"
OWNERS = (
    " AND (task_comment_id IS NULL OR kind = 'task')"
    " AND (requirement_comment_id IS NULL OR kind = 'requirement')"
    " AND (specification_comment_id IS NULL OR kind = 'specification')"
    " AND (test_comment_id IS NULL OR kind = 'test_design')"
)


def upgrade() -> None:
    """既存コメント画像の所属を保ったまま本文用途を追加する。"""
    op.add_column(
        "comment_images",
        sa.Column(
            "retained_at",
            sa.DateTime(timezone=True),
            comment="本文で初めて確定した日時",
        ),
    )
    op.drop_constraint(OWNER_KIND, "comment_images", type_="check")
    op.create_check_constraint(
        OWNER_KIND, "comment_images", f"kind IN ({COMMENT_KINDS},{BODY_KINDS})" + OWNERS
    )


def downgrade() -> None:
    """本文画像が存在する場合は制約でデータを保護する。"""
    op.drop_constraint(OWNER_KIND, "comment_images", type_="check")
    op.create_check_constraint(
        OWNER_KIND, "comment_images", f"kind IN ({COMMENT_KINDS})" + OWNERS
    )
    op.drop_column("comment_images", "retained_at")
