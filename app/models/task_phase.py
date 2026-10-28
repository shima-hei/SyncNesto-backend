"""Projectの工程と変更時点の履歴。"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class ProjectPhase(Base):
    """Taskの親子や状態から独立した工程。"""

    __tablename__ = "project_phases"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_project_phases_project_id"),
        Index(
            "uq_project_phases_name",
            "project_id",
            text("lower(btrim(name))"),
            unique=True,
        ),
        {"comment": db_comment("工程", "Project内の工程とアーカイブ状態")},
    )
    id: Mapped[int] = mapped_column(
        primary_key=True, comment=db_comment("工程ID", "復元後も同じ識別子")
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        index=True,
        comment=db_comment("Project ID", "工程の所有Project"),
    )
    name: Mapped[str] = mapped_column(
        String(100),
        comment=db_comment("工程名", "同一Project内でアーカイブ分を含め一意"),
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        comment=db_comment("表示順", "依存や日程を変更しない表示順"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
        comment=db_comment("版", "楽観的排他制御"),
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment=db_comment("アーカイブ日時", "既存所属や値は保持"),
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), comment=db_comment("作成者", "作成ユーザー")
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), comment=db_comment("更新者", "更新ユーザー")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "工程の作成時刻"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment=db_comment("更新日時", "工程の更新時刻"),
    )


class ProjectPhaseEvent(Base):
    """改名やアーカイブで過去の表示名を書き換えない履歴。"""

    __tablename__ = "project_phase_events"
    __table_args__ = {"comment": db_comment("工程履歴", "変更時点の名前と状態を保持")}
    id: Mapped[int] = mapped_column(
        primary_key=True, comment=db_comment("履歴ID", "工程変更の識別子")
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        index=True,
        comment=db_comment("Project ID", "履歴の所有Project"),
    )
    phase_id: Mapped[int] = mapped_column(
        ForeignKey("project_phases.id"),
        index=True,
        comment=db_comment("工程ID", "変更した工程"),
    )
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), comment=db_comment("変更者", "変更ユーザー")
    )
    old_value: Mapped[dict | None] = mapped_column(
        JSONB, comment=db_comment("変更前", "当時の名称と状態")
    )
    new_value: Mapped[dict] = mapped_column(
        JSONB, comment=db_comment("変更後", "当時の名称と状態")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("変更日時", "変更時刻"),
    )
