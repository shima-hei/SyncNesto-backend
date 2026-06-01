"""プロジェクトに関するSQLAlchemyモデルを定義するモジュール。"""

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class Project(Base):
    """プロジェクトを管理するモデル。"""

    __tablename__ = "projects"
    __table_args__ = {
        "comment": db_comment("プロジェクト", "プロジェクト情報を管理するテーブル"),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("プロジェクトID", "プロジェクトを一意に識別するID"),
    )
    project_code: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
        comment=db_comment(
            "プロジェクトコード",
            "画面表示や外部連携で使用する一意なプロジェクト識別子",
        ),
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=db_comment("プロジェクト名", "画面に表示するプロジェクト名"),
    )
    description: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
        comment=db_comment("説明", "プロジェクトの説明"),
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="active",
        comment=db_comment("ステータス", "プロジェクトの状態"),
    )
    start_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment=db_comment("開始日", "プロジェクト開始日"),
    )
    end_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment=db_comment("終了日", "プロジェクト終了予定日または終了日"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment=db_comment("バージョン", "楽観的排他制御に使用するバージョン番号"),
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("作成者ID", "このプロジェクトを作成したユーザーID"),
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("更新者ID", "このプロジェクトを最後に更新したユーザーID"),
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


class ProjectMember(Base):
    """プロジェクト所属とロールを管理するモデル。"""

    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_members"),
        {
            "comment": db_comment(
                "プロジェクトメンバー",
                "プロジェクト所属とプロジェクト内ロールを管理するテーブル",
            ),
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment(
            "プロジェクトメンバーID",
            "プロジェクトメンバーを一意に識別するID",
        ),
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        index=True,
        comment=db_comment("プロジェクトID", "所属先プロジェクトID"),
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        index=True,
        comment=db_comment("ユーザーID", "所属するユーザーID"),
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id"),
        index=True,
        comment=db_comment("ロールID", "プロジェクト内で付与されたロールID"),
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
