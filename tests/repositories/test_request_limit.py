"""複数instance・並行要求で共有上限を迂回できないことを検証する。"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import session_local
from app.models.request_limit import RequestLimit
from app.repositories.request_limit import RequestLimitRepository


@pytest.fixture
def stable_window(request_limit_clock: dict[str, int]) -> None:
    """同じ時間枠の実DB検証を、実時刻によるskipなしで必ず実行する。"""


def test_counters_shared_across_instances(stable_window: None) -> None:
    """Sessionを作り直しても同じ10回の枠が維持される。"""
    for _ in range(10):
        with session_local() as db:
            assert RequestLimitRepository(db).consume("login:client", 10) == 0
    with session_local() as db:
        assert 1 <= RequestLimitRepository(db).consume("login:client", 10) <= 60
        assert RequestLimitRepository(db).consume("login:another", 10) == 0


def test_parallel_requests_do_not_exceed_limit(stable_window: None) -> None:
    """原子的upsertで競合しても許可する要求は10件だけになる。"""

    def consume(_index: int) -> int:
        """別instance相当のSessionで要求する。"""
        with session_local() as db:
            return RequestLimitRepository(db).consume("login:parallel", 10)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(consume, range(20)))
    assert results.count(0) == 10
    assert all(result == 0 or 1 <= result <= 60 for result in results)


def test_global_limit_bounds_new_ip_counters(db: Session, stable_window: None) -> None:
    """IPを変えても全体上限以降は新しい行を作らない。"""
    repository = RequestLimitRepository(db)
    assert repository.consume("login:first", 10, global_limit=2) == 0
    assert repository.consume("login:second", 10, global_limit=2) == 0
    assert repository.consume("login:third", 10, global_limit=2) > 0
    assert db.scalar(select(func.count()).select_from(RequestLimit)) == 3


def test_expired_counters_cleaned_and_budget_reset(db: Session) -> None:
    """前の時間枠の個人行と全体行を削除してから新しい枠を開始する。"""
    db.add_all(
        [
            RequestLimit(key="login:client", window_start=0, count=10),
            RequestLimit(key="global", window_start=0, count=6000),
            RequestLimit(key="login:old", window_start=0, count=1),
        ]
    )
    db.commit()
    assert RequestLimitRepository(db).consume("login:client", 10) == 0
    assert db.get(RequestLimit, "login:old") is None
    counter = db.get(RequestLimit, "login:client")
    assert counter is not None
    assert counter.count == 1


def test_budget_resets_only_when_window_changes(
    db: Session, request_limit_clock: dict[str, int]
) -> None:
    """同じ枠の拒否と、次の分での削除・再計数を実upsertで確認する。"""
    repository = RequestLimitRepository(db)
    assert repository.consume("login:clock", 1) == 0
    assert repository.consume("login:clock", 1) > 0
    request_limit_clock["seconds"] += 60
    assert repository.consume("login:clock", 1) == 0
    db.expire_all()
    counter = db.get(RequestLimit, "login:clock")
    assert counter is not None
    assert counter.count == 1
    assert counter.window_start == request_limit_clock["seconds"] // 60 * 60
