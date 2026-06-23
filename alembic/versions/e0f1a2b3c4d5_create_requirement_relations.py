"""create requirement relations

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-06-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.comments import db_comment

# revision identifiers, used by Alembic.
revision: str = "e0f1a2b3c4d5"
down_revision: str | Sequence[str] | None = "d9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """requirement_relationsテーブルを作成する。"""
    op.create_table(
        "requirement_relations",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("要件関連ID", "要件関連を一意に識別するID"),
        ),
        sa.Column(
            "document_id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("要件定義書ID", "関連元要件が属する要件定義書ID"),
        ),
        sa.Column(
            "source_requirement_id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("関連元要件ID", "関連元の要件ID"),
        ),
        sa.Column(
            "target_type",
            sa.String(length=100),
            nullable=False,
            comment=db_comment("関連先種別", "関連先リソースの種別"),
        ),
        sa.Column(
            "target_id",
            sa.String(length=255),
            nullable=False,
            comment=db_comment("関連先ID", "関連先リソースのIDまたは外部識別子"),
        ),
        sa.Column(
            "relation_type",
            sa.String(length=100),
            nullable=False,
            comment=db_comment(
                "関連種別",
                "depends_on/blocks/relates_toなどの関連種別",
            ),
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
            comment=db_comment("説明", "関連の補足説明"),
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            nullable=True,
            comment=db_comment("作成者ID", "この関連を作成したユーザーID"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment=db_comment("作成日時", "レコードが作成された日時"),
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["requirement_documents.id"]),
        sa.ForeignKeyConstraint(["source_requirement_id"], ["requirements.id"]),
        sa.PrimaryKeyConstraint("id"),
        comment=db_comment(
            "要件関連",
            "要件と他リソースの意味付き関連を管理するテーブル",
        ),
    )
    op.create_index(
        op.f("ix_requirement_relations_id"),
        "requirement_relations",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_requirement_relations_source",
        "requirement_relations",
        ["source_requirement_id"],
        unique=False,
    )


def downgrade() -> None:
    """requirement_relationsテーブルを削除する。"""
    op.drop_index("ix_requirement_relations_source", table_name="requirement_relations")
    op.drop_index(
        op.f("ix_requirement_relations_id"),
        table_name="requirement_relations",
    )
    op.drop_table("requirement_relations")
