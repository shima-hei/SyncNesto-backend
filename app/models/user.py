"""
DB model : UserテーブルのSQLAlchemyモデルを定義するモジュール
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.db.base import Base
from app.models.comments import db_comment


class User(Base):
    """Userテーブル"""

    __tablename__ = "users"
    __table_args__ = {
        "comment": db_comment("ユーザー", "ユーザー情報を管理するテーブル"),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("ユーザーID", "ユーザーを一意に識別するID"),
    )
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        comment=db_comment("メールアドレス", "ログインと通知に使用するメールアドレス"),
    )
    name: Mapped[str] = mapped_column(
        String(255),
        comment=db_comment("ユーザー名", "画面に表示するユーザー名"),
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        comment=db_comment(
            "ハッシュ化パスワード",
            "Argon2idでハッシュ化したパスワード",
        ),
    )
    department: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=db_comment("部署", "ユーザーが所属する部署"),
    )
    position: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=db_comment("役職", "ユーザーの役職"),
    )
    avatar_key: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
        default=settings.default_avatar_key,
        comment=db_comment(
            "アバターキー",
            "S3に保存したユーザーアイコン画像のオブジェクトキー",
        ),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment=db_comment("有効フラグ", "ログイン可能な有効ユーザーかを示すフラグ"),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=db_comment("最終ログイン日時", "ユーザーが最後にログインした日時"),
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("作成者ID", "このユーザーを作成したユーザーID"),
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("更新者ID", "このユーザーを最後に更新したユーザーID"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment=db_comment("バージョン", "楽観的排他制御に使用するバージョン番号"),
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
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=db_comment("削除日時", "論理削除された日時"),
    )

    def __repr__(self) -> str:
        """デバッグ用のユーザー表現を返す。

        Returns:
            ユーザーIDとemailを含む文字列表現。
        """
        return f"<User id={self.id} email={self.email}>"
