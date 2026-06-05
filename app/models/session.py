"""認証セッションに関するSQLAlchemyモデルを定義するモジュール。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class UserSession(Base):
    """ユーザーのログインセッションを管理するモデル。"""

    __tablename__ = "sessions"
    __table_args__ = {
        "comment": db_comment("セッション", "ログインセッションを管理するテーブル"),
    }

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment=db_comment("セッションID", "ログインセッションを一意に識別するUUID"),
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        index=True,
        comment=db_comment("ユーザーID", "セッションを所有するユーザーID"),
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment=db_comment("開始日時", "セッションが開始された日時"),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment=db_comment("最終アクセス日時", "セッションが最後に確認された日時"),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        comment=db_comment("有効期限", "アイドルタイムアウトの期限日時"),
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        comment=db_comment("絶対有効期限", "延長できない最大セッション期限日時"),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment=db_comment("失効日時", "セッションが失効された日時"),
    )
    revoked_reason: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment=db_comment("失効理由", "logout/expiredなどのセッション失効理由"),
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
