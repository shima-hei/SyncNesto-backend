"""一つの読み取り処理だけで使う、DB Context付きの再取得抑制。"""

from collections.abc import Callable, Generator, Hashable
from contextlib import contextmanager
from typing import TypeVar, cast

from sqlalchemy import event
from sqlalchemy.orm import Session

T = TypeVar("T")
_KEY = "operation_read_cache"
_SCOPES = "operation_read_cache_scopes"


@contextmanager
def read_cache(db: Session) -> Generator[None]:
    """要求間で共有せず、終了時は入れ子の呼び出し元の状態も復元する。"""
    previous = db.info.get(_KEY)
    db.info[_KEY] = {}
    scopes = db.info.setdefault(_SCOPES, [])
    scopes.append(db.info[_KEY])
    try:
        yield
    finally:
        scopes.pop()
        if not scopes:
            db.info.pop(_SCOPES, None)
        if previous is None:
            db.info.pop(_KEY, None)
        else:
            db.info[_KEY] = previous


def cached_read(db: Session, namespace: str, key: Hashable, load: Callable[[], T]) -> T:
    """明示した読み取り範囲内だけで、同じContextの値を再利用する。

    Args:
        db: 要求専用Session。
        namespace: 他用途との衝突を防ぐ名前。
        key: 利用者・対象などの識別子。
        load: 再利用できない場合の取得処理。返す値は変更しないこと。
    """
    cache = db.info.get(_KEY)
    if cache is None or db.new or db.dirty or db.deleted:
        return load()
    context = tuple(
        db.info.get(name) for name in ("data_realm", "tenant_id", "tenant_user_id")
    )
    entry_key = (namespace, key)
    entry = cache.get(entry_key)
    if entry is not None and entry[:2] == (db.get_transaction(), context):
        return cast(T, entry[2])
    value = load()
    cache[entry_key] = (db.get_transaction(), context, value)
    return value


@event.listens_for(Session, "after_flush")
@event.listens_for(Session, "after_transaction_end")
def clear_read_cache(db: Session, *args: object) -> None:
    """書き込み・commit・rollback・savepoint終了時は全て再確認する。"""
    for cache in db.info.get(_SCOPES, []):
        cache.clear()
