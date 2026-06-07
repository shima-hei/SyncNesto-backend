"""監査ログサービスとメンテナンスコマンドのテスト。"""

from argparse import Namespace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from scripts.cleanup_audit_logs import cleanup_audit_logs, validate_args


def create_audit_log(
    db: Session,
    *,
    event_type: str,
    created_at: datetime,
) -> AuditLog:
    """テスト用監査ログを作成する。

    Args:
        db: テスト用DBセッション。
        event_type: イベント種別。
        created_at: 作成日時。

    Returns:
        作成された監査ログ。
    """
    audit_log = AuditLog(
        event_type=event_type,
        extra_metadata={},
        created_at=created_at,
    )
    db.add(audit_log)
    db.commit()
    db.refresh(audit_log)
    return audit_log


def test_validate_cleanup_args_rejects_too_short_retention() -> None:
    """保持日数が短すぎる削除条件を拒否することを確認する。"""
    args = Namespace(older_than_days=1, limit=None)

    with pytest.raises(ValueError, match="older-than-days"):
        validate_args(args)


def test_validate_cleanup_args_rejects_non_positive_limit() -> None:
    """limitが0以下の場合に拒否することを確認する。"""
    args = Namespace(older_than_days=30, limit=0)

    with pytest.raises(ValueError, match="limit"):
        validate_args(args)


def test_cleanup_audit_logs_dry_run_does_not_delete(db: Session) -> None:
    """dry-runでは削除対象件数だけ返し、実削除しないことを確認する。"""
    old_log = create_audit_log(
        db,
        event_type="old.event",
        created_at=datetime.now(UTC) - timedelta(days=120),
    )
    create_audit_log(
        db,
        event_type="recent.event",
        created_at=datetime.now(UTC) - timedelta(days=10),
    )

    target_count = cleanup_audit_logs(
        older_than_days=90,
        limit=None,
        execute=False,
    )

    assert target_count == 1
    assert db.get(AuditLog, old_log.id) is not None
    assert db.query(AuditLog).count() == 2


def test_cleanup_audit_logs_deletes_old_logs(db: Session) -> None:
    """execute指定時に古い監査ログだけ削除することを確認する。"""
    old_log = create_audit_log(
        db,
        event_type="old.event",
        created_at=datetime.now(UTC) - timedelta(days=120),
    )
    recent_log = create_audit_log(
        db,
        event_type="recent.event",
        created_at=datetime.now(UTC) - timedelta(days=10),
    )
    old_log_id = old_log.id
    recent_log_id = recent_log.id

    deleted_count = cleanup_audit_logs(
        older_than_days=90,
        limit=None,
        execute=True,
    )

    db.expire_all()
    assert deleted_count == 1
    assert db.get(AuditLog, old_log_id) is None
    assert db.get(AuditLog, recent_log_id) is not None


def test_cleanup_audit_logs_respects_limit(db: Session) -> None:
    """削除件数上限が指定された場合にlimit件だけ削除することを確認する。"""
    for index in range(3):
        create_audit_log(
            db,
            event_type=f"old.event.{index}",
            created_at=datetime.now(UTC) - timedelta(days=120 + index),
        )

    deleted_count = cleanup_audit_logs(
        older_than_days=90,
        limit=2,
        execute=True,
    )

    assert deleted_count == 2
    assert db.query(AuditLog).count() == 1
