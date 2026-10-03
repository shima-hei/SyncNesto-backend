"""複数のAPI実行環境で共有する短期リクエストカウンター。"""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class RequestLimit(Base):
    """IPを秘密鍵でハッシュした識別子と全体上限を管理する。"""

    __tablename__ = "request_limits"
    __table_args__ = {"comment": db_comment("API回数制限", "一分間の共有カウンター")}

    key: Mapped[str] = mapped_column(
        String(80),
        primary_key=True,
        comment=db_comment("識別子", "用途とIPのHMAC、または全体上限の識別子"),
    )
    window_start: Mapped[int] = mapped_column(
        Integer,
        index=True,
        comment=db_comment("時間枠の開始", "UTCのUnix秒で表す一分間の開始時刻"),
    )
    count: Mapped[int] = mapped_column(
        Integer,
        comment=db_comment("回数", "時間枠内の要求回数"),
    )
