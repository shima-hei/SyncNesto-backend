"""運営承認後の組織発行と、期限付き初回パスワードの案内メール。"""

import secrets
from datetime import UTC, datetime
from html import escape
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import (
    DuplicateResourceError,
    EmailUnavailableError,
    NotFoundError,
)
from app.core.security import get_password_hash
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.account_action import AccountActionRepository
from app.repositories.tenant import TenantRepository
from app.schemas.tenant import TenantCreate, TenantIssue, TenantIssued, TenantRead
from app.services.audit_log import AuditLogService
from app.services.email import EmailService, OutgoingEmail
from app.services.onboarding import mark_initial_password
from app.services.tenant import TenantService


class TenantIssuanceService:
    """組織作成は原子的に保存し、配信失敗は作成済み状態として返す。"""

    def issue(self, db: Session, data: TenantIssue, actor: int) -> TenantIssued:
        """既存Identityの認証情報を保持し、新規Ownerだけ初回パスワードを生成する。"""
        repository = TenantRepository()
        user = repository.identity(db, str(data.owner_email))
        password = None
        if user is None:
            password = secrets.token_urlsafe(24)
            user = User(
                email=str(data.owner_email),
                name=data.owner_name,
                hashed_password=get_password_hash(password),
                created_by=actor,
            )
            mark_initial_password(user)
            db.add(user)
            try:
                db.flush()
            except IntegrityError as exc:
                db.rollback()
                raise DuplicateResourceError() from exc
        tenant = TenantService().create(
            db,
            TenantCreate(name=data.name, slug=data.slug, owner_email=data.owner_email),
            actor,
        )
        delivery = self._deliver(db, tenant, user, actor, password)
        return TenantIssued(
            tenant=TenantRead.model_validate(tenant), email_delivery=delivery
        )

    def resend(
        self, db: Session, tenant_id: int, email: str, actor: int
    ) -> TenantIssued:
        """初回設定待ちのOwnerだけパスワードを再発行し、通常アカウントは案内だけ送る。"""
        repository = AccountActionRepository()
        # Identity→Tenantの順でロックし、本人確認処理と順序を揃える。
        user_id = repository.user_id_by_email(db, email)
        user = repository.get_user(db, user_id) if user_id is not None else None
        tenant = TenantRepository().lock(db, tenant_id)
        if (
            user is None
            or tenant is None
            or repository.tenant_role(db, tenant_id, user.id) != "tenant_owner"
        ):
            raise NotFoundError()
        password = None
        if user.password_change_required:
            password = secrets.token_urlsafe(24)
            user.hashed_password = get_password_hash(password)
            mark_initial_password(user)
            user.version += 1
            user.updated_by = actor
            now = datetime.now(UTC)
            repository.revoke_sessions(db, user.id, now)
            repository.revoke_actions(db, user.id, now)
            repository.clear_login_lock(db, user.email)
        db.commit()
        delivery = self._deliver(db, tenant, user, actor, password)
        return TenantIssued(
            tenant=TenantRead.model_validate(tenant), email_delivery=delivery
        )

    def _deliver(
        self, db: Session, tenant: Tenant, user: User, actor: int, password: str | None
    ) -> Literal["sent", "failed"]:
        """平文パスワードはメール本文のメモリ内だけで扱い、結果を監査する。"""
        login_url = settings.frontend_public_url.rstrip("/") + "/login"
        reset_url = settings.frontend_public_url.rstrip("/") + "/forgot-password"
        text = (
            f"{tenant.name}の利用環境を発行しました。\n\n"
            f"ログイン先: {login_url}\nメールアドレス: {user.email}\n"
        )
        if password is not None:
            assert user.initial_password_expires_at is not None
            expires = user.initial_password_expires_at.astimezone(
                ZoneInfo("Asia/Tokyo")
            ).strftime("%Y/%m/%d %H:%M（日本時間）")
            text += (
                f"初回パスワード: {password}\n有効期限: {expires}\n\n"
                "初回ログイン後、ご自身のパスワードを設定してください。"
                "設定完了まで業務機能は利用できません。\n"
            )
        else:
            text += (
                "\n登録済みのアカウントでログインしてください。"
                "既存のパスワードは変更していません。\n"
            )
        text += (
            f"\nパスワードが分からない・期限が切れた場合: {reset_url}\n"
            "心当たりがない場合は、このメールを破棄してください。"
        )
        delivery: Literal["sent", "failed"] = "sent"
        try:
            EmailService().send(
                OutgoingEmail(
                    to=user.email,
                    subject="[Syncnesto] 利用環境の発行案内",
                    text=text,
                    html=f"<p>{escape(text).replace(chr(10), '<br>')}</p>",
                    idempotency_key=f"tenant-welcome/{uuid4()}",
                )
            )
        except EmailUnavailableError:
            delivery = "failed"
        AuditLogService().record(
            db,
            event_type=f"tenant.welcome_email.{delivery}",
            tenant_id=tenant.id,
            actor_user_id=actor,
            target_user_id=user.id,
            resource_type="tenant",
            resource_id=tenant.id,
        )
        return delivery
