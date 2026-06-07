"""ログイン試行回数に関するSQLAlchemyモデルを定義するモジュール。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class LoginAttempt(Base):
    """ログイン試行回数をemail単位で管理するモデル。"""

    __tablename__ = "login_attempts"
    __table_args__ = {
        "comment": db_comment(
            "ログイン試行",
            "ログイン失敗回数と一時ロック状態を管理するテーブル",
        ),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        comment=db_comment("ログイン試行ID", "ログイン試行を一意に識別するID"),
    )
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        comment=db_comment("メールアドレス", "小文字化したログイン試行対象email"),
    )
    failed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment=db_comment("失敗回数", "連続ログイン失敗回数"),
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment=db_comment("ロック期限", "ログインを一時拒否する期限日時"),
    )
    last_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=db_comment("最終失敗日時", "最後にログイン失敗した日時"),
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
