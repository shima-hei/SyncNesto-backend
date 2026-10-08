"""メール所有者の明示操作による共有Identityの認証情報変更。"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from html import escape
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.config import settings
from app.core.exceptions import (
    AccountActionInvalidError,
    BadRequestError,
    EmailAlreadyRegisteredError,
    EmailUnavailableError,
    ForbiddenError,
    InitialPasswordReuseError,
)
from app.core.security import get_password_hash, verify_password
from app.db.session import session_local
from app.models.account_action import AccountAction, AccountActionPurpose
from app.models.user import User
from app.repositories.account_action import AccountActionRepository
from app.schemas.account_action import AccountActionInspection
from app.services.audit_log import AuditLogService
from app.services.email import EmailService, OutgoingEmail

logger = logging.getLogger(__name__)


class AccountActionService:
    """Userロック、使い捨てリンク、メール送信、認証情報変更を組み合わせる。"""

    def __init__(self, email_service: EmailService | None = None) -> None:
        """送信アダプターを注入でき、テストでは外部メールを送らない。"""
        self.repository = AccountActionRepository()
        self.email_service = email_service or EmailService()
        self.audit = AuditLogService()

    def public_password_reset(self, email: str) -> None:
        """公開HTTP応答の後に実行し、Identity有無と送信時間の推測を防ぐ。

        送信失敗でも既存パスワード・セッション・未使用リンクは変更しない。
        生のトークン、宛先、例外の本文をログへ出さない。
        """
        try:
            with session_local() as db:
                user_id = self.repository.user_id_by_email(db, email)
                if user_id is None:
                    return
                try:
                    self.request_password_reset(db, user_id=user_id)
                except Exception:
                    db.rollback()
                    self.audit.record(
                        db,
                        event_type="account.password_reset.send_failed",
                        target_user_id=user_id,
                        resource_type="user",
                        resource_id=user_id,
                    )
                    logger.warning("Account confirmation mail was not accepted")
        except Exception:
            # 応答済みの公開要求ではDB/配信例外を外へ漏らさず、宛先もログに含めない。
            logger.warning("Public account confirmation request could not be completed")

    def request_password_reset(
        self,
        db: Session,
        *,
        user_id: int,
        actor_id: int | None = None,
        tenant_id: int | None = None,
    ) -> None:
        """管理者もパスワードを指定せず、現在の登録メールへ再設定を依頼する。"""
        if db.info.get("demo_id"):
            self._request_user(db, user_id, actor_id, tenant_id)
            return
        self.email_service.ensure_available()
        user = self._request_user(db, user_id, actor_id, tenant_id)
        action = self._issue(
            db, user, AccountActionPurpose.PASSWORD_RESET, actor_id, tenant_id
        )
        db.commit()
        self._audit(db, "password_reset.requested", action)

    def request_email_change(
        self,
        db: Session,
        *,
        user_id: int,
        actor_id: int,
        new_email: str,
        tenant_id: int | None = None,
    ) -> None:
        """旧アドレス承認を送信し、ログイン情報は変更しない。"""
        if db.info.get("demo_id"):
            self._request_user(db, user_id, actor_id, tenant_id)
            return
        self.email_service.ensure_available()
        user = self._request_user(db, user_id, actor_id, tenant_id)
        self._check_new_email(db, user, new_email)
        action = self._issue(
            db,
            user,
            AccountActionPurpose.EMAIL_CHANGE_APPROVE,
            actor_id,
            tenant_id,
            new_email,
        )
        db.commit()
        self._audit(db, "email_change.requested", action)

    def inspect(self, db: Session, token: str) -> AccountActionInspection:
        """リンク表示時に状態だけを確認し、消費や送信を行わない。"""
        action, _ = self._valid_action(db, token)
        return AccountActionInspection(
            purpose=AccountActionPurpose(action.purpose),
            expires_at=action.expires_at,
            new_email=action.new_email,
        )

    def reset_password(self, db: Session, token: str, password: str) -> str:
        """本人が指定したパスワードと全セッション失効を同時に確定する。"""
        action, user = self._valid_action(
            db, token, AccountActionPurpose.PASSWORD_RESET
        )
        if user.password_change_required and verify_password(
            password, user.hashed_password
        ):
            raise InitialPasswordReuseError()
        user.hashed_password = get_password_hash(password)
        user.password_change_required = False
        user.initial_password_expires_at = None
        user.version += 1
        user.updated_by = user.id
        self._finish(db, action, user)
        self._audit(db, "password_reset.completed", action, completed=True)
        return user.email

    def approve_email_change(self, db: Session, token: str) -> None:
        """旧メール受信者のボタン操作後、新しいアドレスへ確認を送る。"""
        self.email_service.ensure_available()
        action, user = self._valid_action(
            db, token, AccountActionPurpose.EMAIL_CHANGE_APPROVE
        )
        assert action.new_email is not None
        self._check_new_email(db, user, action.new_email)
        # 送信受付前に旧リンクを消費しない。失敗時は再度承認できる。
        self._issue(
            db,
            user,
            AccountActionPurpose.EMAIL_CHANGE_VERIFY,
            action.requested_by_id,
            action.tenant_id,
            action.new_email,
        )
        action.consumed_at = datetime.now(timezone.utc)
        db.commit()
        self._audit(db, "email_change.approved", action)

    def confirm_email_change(self, db: Session, token: str) -> tuple[str, str]:
        """新メール受信者の確認後、両方の確認が揃った変更を確定する。"""
        action, user = self._valid_action(
            db, token, AccountActionPurpose.EMAIL_CHANGE_VERIFY
        )
        assert action.new_email is not None
        self._check_new_email(db, user, action.new_email)
        old_email = user.email
        user.email = action.new_email
        user.version += 1
        user.updated_by = user.id
        try:
            self._finish(db, action, user)
        except IntegrityError:
            db.rollback()
            raise EmailAlreadyRegisteredError() from None
        self._audit(db, "email_change.completed", action, completed=True)
        return old_email, user.email

    def notify_credentials_changed(self, email: str) -> None:
        """完了通知はパスワードやリンクを含まず、失敗しても変更を取り消さない。"""
        text = (
            "Syncnestoのログイン情報が変更されました。全組織のログインを終了しました。\n"
            "心当たりがない場合はシステム運営者へ連絡してください。"
        )
        try:
            self.email_service.send(
                OutgoingEmail(
                    to=email,
                    subject="[Syncnesto] ログイン情報の変更完了",
                    text=text,
                    html=f"<p>{escape(text)}</p>",
                    idempotency_key=f"account-notice/{uuid4()}",
                )
            )
        except EmailUnavailableError:
            logger.warning("Account change notification mail was not accepted")

    def _request_user(
        self, db: Session, user_id: int, actor_id: int | None, tenant_id: int | None
    ) -> User:
        """対象Identityと申請時の組織認可を確認する。"""
        user = self.repository.get_user(db, user_id)
        if user is None:
            raise ForbiddenError()
        self._check_request_authority(db, user_id, actor_id, tenant_id)
        return user

    def _check_request_authority(
        self, db: Session, user_id: int, actor_id: int | None, tenant_id: int | None
    ) -> None:
        """組織管理者の権限と双方の有効所属を、確認時にも再検証する。"""
        if tenant_id is None:
            if actor_id is not None and actor_id != user_id:
                raise ForbiddenError()
            return
        if actor_id is None:
            raise ForbiddenError()
        actor_role = self.repository.tenant_role(db, tenant_id, actor_id)
        target_role = self.repository.tenant_role(db, tenant_id, user_id)
        if (
            actor_role not in {"tenant_owner", "tenant_admin"}
            or target_role is None
            or (actor_role != "tenant_owner" and target_role == "tenant_owner")
        ):
            raise ForbiddenError()

    def _check_new_email(self, db: Session, user: User, new_email: str) -> None:
        """既存Identityの所有メールを奪わない。最終確定時にも再確認する。"""
        if new_email == user.email:
            raise BadRequestError(error_messages.ACCOUNT_ACTION_SAME_EMAIL)
        if self.repository.email_in_use(db, new_email):
            raise EmailAlreadyRegisteredError()

    @staticmethod
    def _fingerprint(user: User) -> str:
        """本人確認を当時のメールとパスワードに結び付ける。"""
        return hmac.new(
            settings.secret_key.encode(),
            f"{user.id}:{user.email}:{user.hashed_password}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def _issue(
        self,
        db: Session,
        user: User,
        purpose: AccountActionPurpose,
        actor_id: int | None,
        tenant_id: int | None,
        new_email: str | None = None,
    ) -> AccountAction:
        """暗号学的乱数を生成し、DBにはハッシュと送信受付IDだけ保存する。"""
        raw_token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        action = AccountAction(
            id=uuid4(),
            user_id=user.id,
            requested_by_id=actor_id,
            tenant_id=tenant_id,
            purpose=purpose.value,
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            credential_fingerprint=self._fingerprint(user),
            old_email=user.email,
            new_email=new_email,
            expires_at=now + timedelta(seconds=settings.account_action_expire_seconds),
        )
        db.add(action)
        db.flush()
        path = (
            "/reset-password"
            if purpose == AccountActionPurpose.PASSWORD_RESET
            else "/confirm-email-change"
        )
        link = f"{settings.frontend_public_url.rstrip('/')}{path}#token={raw_token}"
        minutes = settings.account_action_expire_seconds // 60
        if purpose == AccountActionPurpose.PASSWORD_RESET:
            subject = "パスワードの再設定"
            explanation = (
                "受信者本人が新しいパスワードを設定してください。"
                "管理者はパスワードを指定できません。"
            )
        elif purpose == AccountActionPurpose.EMAIL_CHANGE_APPROVE:
            subject = "メールアドレス変更の承認"
            explanation = (
                f"変更先: {new_email}\n"
                "このメールで承認後、変更先への確認メールを送ります。"
            )
        else:
            subject = "新しいメールアドレスの確認"
            explanation = (
                "現在のメールで承認済みです。"
                "このメールで確認するとメールアドレスを変更します。"
            )
        requester = (
            "本人の申請"
            if actor_id is None or actor_id == user.id
            else "組織管理者による申請"
        )
        text = (
            f"{requester}: {subject}\n{explanation}\n\n"
            f"{link}\n\n有効期限は{minutes}分です。全組織で共用するログイン情報に反映されます。"
            "\n心当たりがない場合は操作せず、このメールを破棄してください。"
        )
        html = (
            f"<p>{escape(requester)}: {escape(subject)}</p><p>{escape(explanation)}</p>"
            f'<p><a href="{escape(link, quote=True)}">{escape(subject)}へ進む</a></p>'
            f"<p>有効期限は{minutes}分です。全組織で共用するログイン情報に反映されます。</p>"
            "<p>心当たりがない場合は操作せず、このメールを破棄してください。</p>"
        )
        recipient = (
            new_email
            if purpose == AccountActionPurpose.EMAIL_CHANGE_VERIFY
            else user.email
        )
        assert recipient is not None
        try:
            action.provider_message_id = self.email_service.send(
                OutgoingEmail(
                    to=recipient,
                    subject=f"[Syncnesto] {subject}",
                    text=text,
                    html=html,
                    idempotency_key=f"account-action/{action.id}",
                )
            )
        except EmailUnavailableError:
            db.rollback()
            raise
        action.sent_at = datetime.now(timezone.utc)
        return action

    def _valid_action(
        self, db: Session, token: str, purpose: AccountActionPurpose | None = None
    ) -> tuple[AccountAction, User]:
        """同じUserのリンクを直列化し、有効期限・権限・認証情報を検証する。"""
        digest = hashlib.sha256(token.encode()).hexdigest()
        candidate = self.repository.by_hash(db, digest)
        if candidate is None:
            raise AccountActionInvalidError()
        user = self.repository.get_user(db, candidate.user_id)
        action = self.repository.by_hash(db, digest, lock=True)
        now = datetime.now(timezone.utc)
        if (
            user is None
            or action is None
            or action.sent_at is None
            or action.consumed_at is not None
            or action.revoked_at is not None
            or action.expires_at <= now
            or action.old_email != user.email
            or not hmac.compare_digest(
                action.credential_fingerprint, self._fingerprint(user)
            )
            or (purpose is not None and action.purpose != purpose.value)
        ):
            raise AccountActionInvalidError()
        try:
            self._check_request_authority(
                db, user.id, action.requested_by_id, action.tenant_id
            )
        except ForbiddenError:
            raise AccountActionInvalidError() from None
        return action, user

    def _finish(self, db: Session, action: AccountAction, user: User) -> None:
        """本人の変更、リンク消費、他リンク失効、全セッション失効を原子的に行う。"""
        now = datetime.now(timezone.utc)
        action.consumed_at = now
        self.repository.revoke_actions(db, user.id, now)
        self.repository.revoke_sessions(db, user.id, now)
        self.repository.clear_login_lock(db, action.old_email)
        if user.email != action.old_email:
            self.repository.clear_login_lock(db, user.email)
        db.commit()

    def _audit(
        self, db: Session, event: str, action: AccountAction, *, completed: bool = False
    ) -> None:
        """トークンやパスワードを含めず、組織・申請者と本人の確定を記録する。"""
        self.audit.record(
            db,
            event_type=f"account.{event}",
            tenant_id=action.tenant_id,
            actor_user_id=action.user_id if completed else action.requested_by_id,
            target_user_id=action.user_id,
            resource_type="user",
            resource_id=action.user_id,
            metadata={
                "action_id": str(action.id),
                "requested_by_id": action.requested_by_id,
                "provider_message_id": action.provider_message_id,
            },
        )
