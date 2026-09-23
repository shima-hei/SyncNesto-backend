"""競合・重複エラー処理の共通ヘルパー。"""

from collections.abc import Iterable, Mapping
from typing import NoReturn

from sqlalchemy.orm import Session

from app.core.exceptions import DuplicateResourceError, VersionConflictError


def raise_if_version_conflict(
    *,
    current_version: int,
    requested_version: int,
    current: dict[str, object],
) -> None:
    """バージョン不一致の場合にVersionConflictErrorを送出する。

    Args:
        current_version: DB上の現在version。
        requested_version: リクエストで指定されたversion。
        current: 競合時に返す最新リソース情報。

    Raises:
        VersionConflictError: versionが一致しない場合。
    """
    if current_version != requested_version:
        raise VersionConflictError(current=current)


def build_conflict_current(
    resource: object,
    fields: Iterable[str],
    *,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """バージョン競合時に返す現行リソース情報を組み立てる。

    Args:
        resource: 現行値を持つSQLAlchemyモデルなどのオブジェクト。
        fields: currentに含める属性名一覧。
        extra: モデル属性以外に追加する値。

    Returns:
        VERSION_CONFLICTレスポンスのcurrentに入れる辞書。
    """
    current = {field: getattr(resource, field) for field in fields}
    if extra is not None:
        current.update(extra)
    return current


def raise_duplicate_after_rollback(
    db: Session,
    message: str,
    exc: Exception,
) -> NoReturn:
    """DBトランザクションをrollbackし、重複エラーを送出する。

    Args:
        db: DBセッション。
        message: 重複エラーのメッセージ。
        exc: 元になった例外。

    Raises:
        DuplicateResourceError: 重複エラー。
    """
    db.rollback()
    raise DuplicateResourceError(message) from exc
