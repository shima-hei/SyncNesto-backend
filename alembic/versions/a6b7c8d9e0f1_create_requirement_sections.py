"""create requirement sections

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-06-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.comments import db_comment

# revision identifiers, used by Alembic.
revision: str = "a6b7c8d9e0f1"
down_revision: str | Sequence[str] | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """requirement_sectionsテーブルとrequirements.section_idを追加する。"""
    op.create_table(
        "requirement_sections",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
            comment=db_comment(
                "要件定義セクションID",
                "要件定義セクションを一意に識別するID",
            ),
        ),
        sa.Column(
            "document_id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("要件定義書ID", "所属する要件定義書ID"),
        ),
        sa.Column(
            "title",
            sa.String(length=255),
            nullable=False,
            comment=db_comment("タイトル", "セクションのタイトル"),
        ),
        sa.Column(
            "section_type",
            sa.String(length=100),
            nullable=False,
            comment=db_comment(
                "セクション種別",
                "overview/scope/businessなどのセクション種別",
            ),
        ),
        sa.Column(
            "content",
            sa.Text(),
            nullable=True,
            comment=db_comment("本文", "セクションの本文"),
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            comment=db_comment("表示順", "要件定義書内での表示順"),
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            comment=db_comment("ステータス", "セクションの状態"),
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
            comment=db_comment("作成者ID", "このセクションを作成したユーザーID"),
        ),
        sa.Column(
            "updated_by",
            sa.Integer(),
            nullable=True,
            comment=db_comment("更新者ID", "このセクションを最後に更新したユーザーID"),
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
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["requirement_documents.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        comment=db_comment(
            "要件定義セクション",
            "要件定義書内の章・節を管理するテーブル",
        ),
    )
    op.create_index(
        op.f("ix_requirement_sections_document_id"),
        "requirement_sections",
        ["document_id"],
    )
    op.create_index(op.f("ix_requirement_sections_id"), "requirement_sections", ["id"])
    op.add_column(
        "requirements",
        sa.Column(
            "section_id",
            sa.Integer(),
            nullable=True,
            comment=db_comment("要件定義セクションID", "所属するセクションID"),
        ),
    )
    op.create_foreign_key(
        op.f("fk_requirements_section_id_requirement_sections"),
        "requirements",
        "requirement_sections",
        ["section_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_requirements_section_id"),
        "requirements",
        ["section_id"],
    )


def downgrade() -> None:
    """requirements.section_idとrequirement_sectionsテーブルを削除する。"""
    op.drop_index(op.f("ix_requirements_section_id"), table_name="requirements")
    op.drop_constraint(
        op.f("fk_requirements_section_id_requirement_sections"),
        "requirements",
        type_="foreignkey",
    )
    op.drop_column("requirements", "section_id")
    op.drop_index(op.f("ix_requirement_sections_id"), table_name="requirement_sections")
    op.drop_index(
        op.f("ix_requirement_sections_document_id"),
        table_name="requirement_sections",
    )
    op.drop_table("requirement_sections")
