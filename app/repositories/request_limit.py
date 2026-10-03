"""PostgreSQLの原子的なupsertでリクエスト回数を管理する。"""

from sqlalchemy import Integer, cast, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.request_limit import RequestLimit


class RequestLimitRepository:
    """全instanceが同じDB時刻・カウンターを使うRepository。"""

    def __init__(self, db: Session) -> None:
        """独立した短いトランザクションで使用するSessionを受け取る。"""
        self.db = db

    def consume(self, key: str, limit: int, global_limit: int = 6000) -> int:
        """上限内なら0、超過なら再試行までの秒数を返す。"""
        now = self.db.scalar(
            select(
                cast(func.floor(func.extract("epoch", func.clock_timestamp())), Integer)
            )
        )
        assert now is not None
        window = now // 60 * 60
        self.db.execute(delete(RequestLimit).where(RequestLimit.window_start < window))
        # 全体上限がIPを増やす攻撃時のカウンター数も制限する。
        global_allowed = self._increment("global", window, global_limit)
        allowed = global_allowed and self._increment(key, window, limit)
        self.db.commit()
        return 0 if allowed else 60 - (now - window)

    def _increment(self, key: str, window: int, limit: int) -> bool:
        """上限に達した行を更新せず、並行要求による超過を防ぐ。"""
        statement = insert(RequestLimit).values(key=key, window_start=window, count=1)
        statement = statement.on_conflict_do_update(
            index_elements=[RequestLimit.key],
            set_={"count": RequestLimit.count + 1},
            where=RequestLimit.count < limit,
        ).returning(RequestLimit.count)
        return self.db.scalar(statement) is not None
