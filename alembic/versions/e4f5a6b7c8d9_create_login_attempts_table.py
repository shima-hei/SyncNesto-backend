"""create login attempts table

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-06-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.comments import db_comment

# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: str | Sequence[str] | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """login_attemptsテーブルを作成する。"""
    op.create_table(
        "login_attempts",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
            comment=db_comment("ログイン試行ID", "ログイン試行を一意に識別するID"),
        ),
        sa.Column(
            "email",
            sa.String(length=255),
            nullable=False,
            comment=db_comment(
                "メールアドレス",
                "小文字化したログイン試行対象email",
            ),
        ),
        sa.Column(
            "failed_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment=db_comment("失敗回数", "連続ログイン失敗回数"),
        ),
        sa.Column(
            "locked_until",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=db_comment("ロック期限", "ログインを一時拒否する期限日時"),
        ),
        sa.Column(
            "last_failed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=db_comment("最終失敗日時", "最後にログイン失敗した日時"),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_login_attempts_email"),
        comment=db_comment(
            "ログイン試行",
            "ログイン失敗回数と一時ロック状態を管理するテーブル",
        ),
    )
    op.create_index(
        op.f("ix_login_attempts_email"),
        "login_attempts",
        ["email"],
        unique=False,
    )
    op.create_index(
        op.f("ix_login_attempts_locked_until"),
        "login_attempts",
        ["locked_until"],
        unique=False,
    )


def downgrade() -> None:
    """login_attemptsテーブルを削除する。"""
    op.drop_index(op.f("ix_login_attempts_locked_until"), table_name="login_attempts")
    op.drop_index(op.f("ix_login_attempts_email"), table_name="login_attempts")
    op.drop_table("login_attempts")
