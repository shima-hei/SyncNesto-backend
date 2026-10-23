"""メール通知の本人選択。ゲストへ組織権限は与えない。"""

import hashlib
import hmac

from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.error_messages import NOTIFICATION_EMAIL_UNAVAILABLE
from app.core.exceptions import (
    ConflictError,
    EmailUnavailableError,
    ForbiddenError,
    NotFoundError,
    VersionConflictError,
)
from app.models.notification_email import NotificationEmailPreference
from app.models.review_account import ReviewAccountGrant
from app.models.user import User
from app.schemas.notification_email import NotificationEmailPreferences
from app.services.email import EmailService
from app.services.publication_access_context import PublicationAccessContext


def local_mail_enabled() -> bool:
    """明示したLOCALだけで新機能を開く。"""
    return (
        settings.fixed_approval_email_enabled
        and settings.fixed_approval_notifications_enabled
        and not settings.is_public_environment
        and not settings.demo_mode
    )


def email_identity(user: User) -> str:
    """Userと現メールを用途分離したHMACで結ぶ。"""
    return hmac.new(
        settings.secret_key.encode(),
        f"approval-mail-v1:{user.id}:{user.email}".encode(),
        hashlib.sha256,
    ).hexdigest()


def capture_available(user: User) -> bool:
    """通常SMTPへ漏らさず、当該本人だけの可用性を返す。"""
    try:
        return local_mail_enabled() and EmailService.is_local_capture(user.email)
    except EmailUnavailableError:
        return False


def pause_email_selection(db: Session, user_id: int) -> None:
    """宛先変更と同じtransactionで旧ONを停止し、元のアドレスでも復活させない。"""
    if (
        settings.is_public_environment
        or settings.demo_mode
        or db.info.get("data_realm") == "demo"
        or db.info.get("mcp_connection_id")
    ):
        return
    # flag停止中にも宛先変更を追跡する。移行前の互換runtimeには新表がない。
    if not inspect(db.connection()).has_table("notification_email_preferences"):
        return
    db.execute(text("SELECT pg_advisory_xact_lock(1398362932,0)"))
    db.execute(
        update(NotificationEmailPreference)
        .where(
            NotificationEmailPreference.user_id == user_id,
            NotificationEmailPreference.enabled.is_(True),
        )
        .values(
            enabled=False,
            version=NotificationEmailPreference.version + 1,
            updated_at=func.clock_timestamp(),
        )
    )


def preference_read(row, user: User) -> NotificationEmailPreferences:
    """宛先変更後の旧ONを実効OFFとして返す。"""
    changed = bool(
        row
        and row.email_digest
        and (not row.enabled or row.email_digest != email_identity(user))
    )
    return NotificationEmailPreferences(
        fixed_approval_email_enabled=bool(row and row.enabled and not changed),
        version=row.version if row else 0,
        available=capture_available(user),
        pause_reason="identity_changed" if changed else None,
    )


class NotificationEmailPreferencesService:
    """本人認証を再検証し、設定行だけを更新する。"""

    def access(self, db, user):
        """デモ・審査・MCP・他人のContextを利用しない。"""
        if not local_mail_enabled():
            raise NotFoundError()
        if db.info.get("data_realm") == "demo" or db.info.get("mcp_connection_id"):
            raise ForbiddenError()
        if db.get(ReviewAccountGrant, user.id) is not None:
            raise ForbiddenError()

    def get(self, db, user):
        """未作成を保存せず返す。"""
        self.access(db, user)
        return preference_read(db.get(NotificationEmailPreference, user.id), user)

    def update(self, original, user, data, expected_user):
        """設定OFFと配送中の共有ロックを直列化する。"""
        self.access(original, user)
        if expected_user != user.id:
            raise ForbiddenError()
        with Session(original.get_bind()) as db, db.begin():
            db.execute(text("SELECT pg_advisory_xact_lock_shared(1398362932,0)"))
            actor = PublicationAccessContext().verify(
                db, original, user, internal=False
            )
            created = (
                db.scalar(
                    insert(NotificationEmailPreference)
                    .values(user_id=actor.id, enabled=False, version=1)
                    .on_conflict_do_nothing()
                    .returning(NotificationEmailPreference.user_id)
                )
                if data.version == 0
                else None
            )
            row = db.scalar(
                select(NotificationEmailPreference)
                .where(NotificationEmailPreference.user_id == actor.id)
                .with_for_update()
            )
            if (data.version == 0 and created is None) or (
                data.version != 0 and (row is None or row.version != data.version)
            ):
                raise VersionConflictError(preference_read(row, actor).model_dump())
            if data.fixed_approval_email_enabled and not capture_available(actor):
                raise ConflictError(NOTIFICATION_EMAIL_UNAVAILABLE)
            assert row is not None
            row.enabled = data.fixed_approval_email_enabled
            row.email_digest = email_identity(actor) if row.enabled else None
            row.version += 1 if data.version else 0
            row.updated_at = db.execute(select(func.clock_timestamp())).scalar_one()
            db.flush()
            return preference_read(row, actor)
