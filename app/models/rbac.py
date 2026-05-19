"""RBACに関するSQLAlchemyモデルを定義するモジュール。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class Role(Base):
    """ロールを管理するモデル。"""

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("key", "scope", name="uq_roles_key_scope"),
        UniqueConstraint("name", "scope", name="uq_roles_name_scope"),
        {
            "comment": db_comment("ロール", "システムロールとプロジェクトロールを管理するテーブル"),
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("ロールID", "ロールを一意に識別するID"),
    )
    key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment=db_comment("ロールキー", "APIと画面制御で使用する安定したロール識別子"),
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment=db_comment("ロール名", "画面表示用のロール名"),
    )
    scope: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        comment=db_comment("スコープ", "ロールの適用範囲。systemまたはproject"),
    )
    description: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=db_comment("説明", "ロールの説明"),
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


class Permission(Base):
    """権限を管理するモデル。"""

    __tablename__ = "permissions"
    __table_args__ = {
        "comment": db_comment("権限", "操作単位の権限を管理するテーブル"),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("権限ID", "権限を一意に識別するID"),
    )
    code: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
        comment=db_comment("権限コード", "API認可判定で使用する権限コード"),
    )
    description: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=db_comment("説明", "権限の説明"),
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


class RolePermission(Base):
    """ロールと権限の対応を管理するモデル。"""

    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permissions"),
        {
            "comment": db_comment("ロール権限", "ロールと権限の対応を管理するテーブル"),
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("ロール権限ID", "ロール権限を一意に識別するID"),
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id"),
        index=True,
        comment=db_comment("ロールID", "権限を付与するロールID"),
    )
    permission_id: Mapped[int] = mapped_column(
        ForeignKey("permissions.id"),
        index=True,
        comment=db_comment("権限ID", "ロールに付与する権限ID"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )


class UserRole(Base):
    """ユーザーとシステムロールの対応を管理するモデル。"""

    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_roles"),
        {
            "comment": db_comment("ユーザーロール", "ユーザーに付与されたシステムロールを管理するテーブル"),
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("ユーザーロールID", "ユーザーロールを一意に識別するID"),
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        index=True,
        comment=db_comment("ユーザーID", "システムロールを付与するユーザーID"),
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id"),
        index=True,
        comment=db_comment("ロールID", "ユーザーに付与するシステムロールID"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )
