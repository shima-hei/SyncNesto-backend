"""初期Ownerの指定と、明示的な初期パスワード設定を検証する。"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import verify_password
from app.models.rbac import Role
from app.models.session import UserSession
from app.models.tenant import TenantMember
from scripts import bootstrap_tenant_owner as bootstrap


def test_bootstrap_is_explicit_idempotent_and_revokes_only_target_sessions(
    db, create_test_user, monkeypatch, tmp_path
):
    """通常実行はパスワードを保持し、指定時だけ本人のセッションを失効する。"""
    owner = create_test_user(email="designated@example.com")
    other = create_test_user(email="another@example.com")
    original_hash = owner.hashed_password
    now = datetime.now(UTC)
    sessions = [
        UserSession(
            user_id=user.id,
            started_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(hours=1),
            absolute_expires_at=now + timedelta(days=1),
        )
        for user in (owner, other)
    ]
    db.add_all(sessions)
    db.commit()

    @contextmanager
    def same_session():
        yield db

    monkeypatch.setattr(bootstrap, "session_local", same_session)
    bootstrap.bootstrap_owner(owner.email)
    member = db.query(TenantMember).filter(TenantMember.user_id == owner.id).one()
    assert db.get(Role, member.role_id).key == "tenant_owner"
    version = member.version
    bootstrap.bootstrap_owner(owner.email)
    db.refresh(member)
    db.refresh(owner)
    assert member.version == version and owner.hashed_password == original_hash
    with pytest.raises(RuntimeError, match="別のOwner"):
        bootstrap.bootstrap_owner(other.email)
    db.rollback()
    secret_file = tmp_path / "initial-password.txt"
    secret_file.write_text("initial-password-used-only-by-test")
    secret_file.chmod(0o600)
    bootstrap.bootstrap_owner(owner.email, secret_file)
    db.refresh(owner)
    for session in sessions:
        db.refresh(session)
    assert verify_password(secret_file.read_text(), owner.hashed_password)
    assert sessions[0].revoked_reason == "initial_tenant_owner_credentials"
    assert sessions[1].revoked_at is None
