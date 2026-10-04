"""Default Tenantの初期Ownerを明示的に設定する運用スクリプト。"""

import argparse
from datetime import UTC, datetime
from pathlib import Path

from app.core.security import get_password_hash
from app.db.session import session_local
from app.models.rbac import Role
from app.models.session import UserSession
from app.models.tenant import Tenant, TenantMember
from app.models.user import User
from app.services.audit_log import AuditLogService


def bootstrap_owner(email: str, password_file: Path | None = None) -> None:
    """指定された既存の有効ユーザーを初期Ownerにする。"""
    with session_local() as db:
        tenant = (
            db.query(Tenant).filter(Tenant.slug == "default").with_for_update().one()
        )
        user = (
            db.query(User)
            .filter(
                User.email == email, User.is_active.is_(True), User.deleted_at.is_(None)
            )
            .one_or_none()
        )
        if user is None:
            raise RuntimeError("指定された有効なIdentityが存在しません")
        owner_role = (
            db.query(Role)
            .filter(Role.key == "tenant_owner", Role.scope == "tenant")
            .one()
        )
        existing = (
            db.query(TenantMember)
            .filter(
                TenantMember.tenant_id == tenant.id,
                TenantMember.role_id == owner_role.id,
                TenantMember.user_id != user.id,
                TenantMember.status == "active",
            )
            .first()
        )
        if existing is not None:
            raise RuntimeError(
                "別のOwnerが既に存在します。初期化ではOwnerを変更しません"
            )
        member = (
            db.query(TenantMember)
            .filter(
                TenantMember.tenant_id == tenant.id, TenantMember.user_id == user.id
            )
            .one()
        )
        if member.role_id != owner_role.id or member.status != "active":
            member.role_id = owner_role.id
            member.status = "active"
            member.version += 1
        if password_file is not None:
            password = password_file.read_text().strip()
            if not password:
                raise RuntimeError("パスワードファイルが空です")
            user.hashed_password = get_password_hash(password)
            user.version += 1
            db.query(UserSession).filter(
                UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
            ).update(
                {
                    UserSession.revoked_at: datetime.now(UTC),
                    UserSession.revoked_reason: "initial_tenant_owner_credentials",
                },
                synchronize_session=False,
            )
        db.commit()
        AuditLogService().record(
            db,
            event_type="tenant.owner_bootstrapped",
            tenant_id=tenant.id,
            target_user_id=user.id,
            resource_type="tenant",
            resource_id=tenant.id,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--password-file",
        type=Path,
        help="明示指定時だけ初期ログイン情報も設定する。既存セッションは失効する",
    )
    args = parser.parse_args()
    bootstrap_owner(args.email, args.password_file)
