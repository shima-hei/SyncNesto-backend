"""工数見積の再計算に必要な入力と改訂履歴。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
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
    quantity: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), comment=db_comment("数量", "見積対象の数量")
    )
    unit_hours: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), comment=db_comment("単位工数", "数量1単位あたりの時間")
    )
    reference_work: Mapped[str | None] = mapped_column(
        String(200), comment=db_comment("類似作業", "参照した作業の名称")
    )
    reference_minutes: Mapped[int | None] = mapped_column(
        Integer, comment=db_comment("参照分数", "類似作業の工数")
    )
    factor: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), comment=db_comment("補正係数", "参照工数に掛ける倍率")
    )
    assumptions: Mapped[str | None] = mapped_column(String(2000))
    change_reason: Mapped[str | None] = mapped_column(String(500))
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TaskEstimateBaseline(Base):
    __tablename__ = "task_estimate_baselines"
    __table_args__ = {
        "comment": db_comment(
            "基準見積の確定履歴", "確定対象の見積改訂と確定した人を保持"
        )
    }

    id: Mapped[int] = mapped_column(primary_key=True)
    estimate_revision_id: Mapped[int] = mapped_column(
        ForeignKey("task_estimate_revisions.id", ondelete="CASCADE"), unique=True
    )
    confirmed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reason: Mapped[str | None] = mapped_column(String(500))
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
