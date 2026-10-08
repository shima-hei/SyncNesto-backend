"""専用DBの初期化と受付停止後の回収の境界を検証する。"""

import json
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.models.demo import DemoSession
from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.user import User
from scripts import cleanup_demo, seed_rbac


def test_roles_only_seeds_rbac_without_initial_credentials_or_identity(db, monkeypatch):
    """空DBでRBACだけを冪等に初期化し、通常の管理者を作らない。"""
    for model in (RolePermission, Permission, Role):
        db.execute(delete(model))
    db.commit()

    @contextmanager
    def same_session():
        yield db

    monkeypatch.setattr(seed_rbac, "session_local", same_session)
    monkeypatch.setattr(settings, "initial_admin_email", None)
    monkeypatch.setattr(settings, "initial_admin_password", None)
    seed_rbac.seed_rbac(roles_only=True)
    seed_rbac.seed_rbac(roles_only=True)
    assert db.scalar(select(func.count()).select_from(Role)) == len(seed_rbac.ROLES)
    assert db.scalar(select(func.count()).select_from(Permission)) == len(
        seed_rbac.PERMISSIONS
    )
    assert db.scalar(select(func.count()).select_from(RolePermission)) > 0
    assert db.scalar(select(func.count()).select_from(User)) == 0
    assert db.scalar(select(func.count()).select_from(UserRole)) == 0


def test_default_seed_still_creates_initial_system_admin(db, monkeypatch):
    """既存の通常初期化では、明示した管理者の作成を維持する。"""
    monkeypatch.setattr(settings, "initial_admin_email", "seed-admin@example.com")
    monkeypatch.setattr(settings, "initial_admin_password", "test-only-seed-password")
    seed_rbac.seed_rbac()
    user = db.scalar(select(User).where(User.email == "seed-admin@example.com"))
    assert user is not None
    role = db.scalar(
        select(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id)
    )
    assert role is not None and role.key == "system_admin" and role.scope == "system"


@pytest.mark.no_db
def test_cleanup_refuses_unverified_database_before_access(monkeypatch):
    """分離未確認ならdry-runも実行もDBへ接続しない。"""
    monkeypatch.setattr(settings, "demo_data_isolated", False)
    for execute in (False, True):
        with pytest.raises(ValueError, match="dedicated database"):
            cleanup_demo.cleanup_demo(execute=execute)


def test_cleanup_runs_with_demo_disabled_and_preserves_dry_run(
    db, monkeypatch, demo_settings
):
    """受付停止中も確認でき、実行後に期限切れの台帳だけが完了する。"""
    now = datetime.now(UTC)
    record = DemoSession(
        created_at=now - timedelta(hours=2),
        expires_at=now - timedelta(hours=1),
        absolute_expires_at=now - timedelta(hours=1),
        cleanup_after=now - timedelta(hours=1),
        status="cleanup_pending",
        revoked_at=now - timedelta(hours=1),
        revoked_reason="expired",
    )
    db.add(record)
    db.commit()
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "demo_data_isolated", True)
    result = cleanup_demo.cleanup_demo()
    assert result.processed == 0 and result.pending == 1
    db.refresh(record)
    assert record.status == "cleanup_pending"
    result = cleanup_demo.cleanup_demo(execute=True)
    assert result.processed == 1 and result.pending == 0
    db.refresh(record)
    assert record.status == "cleaned"


def test_cleanup_summary_includes_deferred_receipts(
    db, monkeypatch, capsys, demo_settings
):
    """今回の対象が0でも、遅延PUT待ちの残件を完了と誤認させない。"""
    now = datetime.now(UTC)
    db.add(
        DemoSession(
            created_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
            absolute_expires_at=now - timedelta(hours=1),
            cleanup_after=now + timedelta(minutes=10),
            status="cleanup_pending",
            revoked_at=now - timedelta(hours=1),
            revoked_reason="expired",
        )
    )
    db.commit()
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "demo_data_isolated", True)
    monkeypatch.setattr(sys, "argv", ["cleanup_demo", "--json", "--execute"])
    assert cleanup_demo.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "processed": 0,
        "pending": 0,
        "remaining": 1,
        "execute": True,
        "limit": 10,
    }
