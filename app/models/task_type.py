"""案件ごとに名前を管理し、非表示後も参照キーを保持する。"""

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class ProjectTaskType(Base):
    """標準種別を変更しない独自種別カタログ。"""

    __tablename__ = "project_task_types"
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_project_task_types_key"),
        UniqueConstraint("project_id", "name_key", name="uq_project_task_types_name"),
        {"comment": db_comment("案件別タスク種別", "非表示後も内部キーと名前を保持")},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    name_key: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true()
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
