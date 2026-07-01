"""下書き保存モデルを定義するモジュール。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class Draft(Base):
    """フォーム入力中の下書きを管理するモデル。"""

    __tablename__ = "drafts"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "scope_key",
            name="uq_drafts_owner_scope",
        ),
        {
            "comment": db_comment(
                "下書き",
                "フォーム入力中の下書きをユーザーとスコープ単位で管理するテーブル",
            ),
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("下書きID", "下書きを一意に識別するID"),
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
        comment=db_comment("所有ユーザーID", "下書きを所有するユーザーID"),
    )
    scope_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment=db_comment("スコープキー", "画面と対象リソースを識別するキー"),
    )
    resource_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment=db_comment("リソース種別", "下書き対象のリソース種別"),
    )
    resource_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment=db_comment("リソースID", "下書き対象の正式リソースID"),
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"),
        nullable=True,
        index=True,
        comment=db_comment("プロジェクトID", "下書き対象のプロジェクトID"),
    )
    schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment=db_comment("スキーマバージョン", "フォーム構造のバージョン"),
    )
    content: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        comment=db_comment("下書き内容", "フォーム入力値JSON"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment=db_comment("バージョン", "楽観的排他制御に使用するバージョン番号"),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment=db_comment("有効期限", "下書きが失効する日時"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment=db_comment("更新日時", "レコードが最後に更新された日時"),
    )
