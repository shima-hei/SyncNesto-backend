"""工数見積の再計算に必要な入力と改訂履歴。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class TaskEstimateRevision(Base):
    __tablename__ = "task_estimate_revisions"
    __table_args__ = (
        UniqueConstraint("task_id", "task_version"),
        {"comment": db_comment("工数見積改訂", "工数見積を算出した入力と保存時点の値")},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    task_version: Mapped[int] = mapped_column(Integer)
    method: Mapped[str | None] = mapped_column(String(16))
    minutes: Mapped[int | None] = mapped_column(Integer)
    optimistic_minutes: Mapped[int | None] = mapped_column(Integer)
    likely_minutes: Mapped[int | None] = mapped_column(Integer)
    pessimistic_minutes: Mapped[int | None] = mapped_column(Integer)
    assumptions: Mapped[str | None] = mapped_column(String(2000))
    change_reason: Mapped[str | None] = mapped_column(String(500))
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
