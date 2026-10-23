"""固定対象の最小概要だけをメールへ投影する。"""

import hashlib
import json
from html import escape
from urllib.parse import urlsplit

from sqlalchemy import select

from app.core.config import settings
from app.models.notification_email import NotificationEmailPreference
from app.models.review_account import ReviewAccountGrant
from app.models.user import User
from app.repositories.fixed_approval_notification import (
    FixedApprovalNotificationRepository,
)
from app.services.email import OutgoingEmail
from app.services.notification_email_preferences import (
    capture_available,
    email_identity,
)

LABELS = {
    "requirement": "要件",
    "requirement_document": "要件定義書",
    "specification": "設計",
    "test_design": "テスト設計",
}
EVENTS = {
    "requested": ("確認依頼があります", "確認依頼が届きました"),
    "cancelled": ("確認依頼が取り消されました", "確認依頼が取り消されました"),
    "approved": ("確認依頼の結果が更新されました", "結果が更新されました"),
    "approved_with_exceptions": (
        "確認依頼の結果が更新されました",
        "結果が更新されました",
    ),
    "rejected": ("確認依頼の結果が更新されました", "結果が更新されました"),
}


def eligible_notification(db, notification_id):
    """JSONではなく現在の正本・資格で宛先を解決する。"""
    repo = FixedApprovalNotificationRepository()
    base = (
        db.execute(repo.base().where(repo.n.c.id == notification_id)).mappings().first()
    )
    if base is None:
        return None
    recipient = base["recipient_user_id"]
    external = (
        base["public_context"] is not None
        and base["action"] in {"requested", "cancelled"}
        and db.scalar(
            select(repo.r.c.approver_id).where(repo.r.c.id == base["approval_id"])
        )
        == recipient
    )
    query = (
        repo.shared(recipient)
        if external
        else repo.internal(recipient, base["tenant_id"])
    )
    item = db.execute(query.where(repo.n.c.id == notification_id)).mappings().first()
    if item is None or (
        item["action"] == "requested" and item["current_status"] != "requested"
    ):
        return None
    user = db.get(User, recipient, populate_existing=True)
    preference = db.scalar(
        select(NotificationEmailPreference)
        .where(NotificationEmailPreference.user_id == recipient)
        .execution_options(populate_existing=True)
    )
    if (
        user is None
        or not user.is_active
        or user.deleted_at is not None
        or user.password_change_required
        or db.get(ReviewAccountGrant, recipient) is not None
        or preference is None
        or not preference.enabled
        or preference.email_digest != email_identity(user)
        or not capture_available(user)
    ):
        return None
    return item, user, preference, external


def render_email(item, user, external, delivery_id):
    """任意URL・氏名・本文・判断結果を取り込まない。"""
    origin = urlsplit(settings.frontend_public_url)
    if (
        origin.scheme != "http"
        or origin.hostname not in {"localhost", "127.0.0.1", "::1"}
        or origin.username
        or origin.password
        or origin.path not in {"", "/"}
        or origin.query
        or origin.fragment
    ):
        raise ValueError("local_frontend_origin_required")
    title = (
        item["public_context"]["title"] if external else item["fixed_scope"]["title"]
    )
    title = " ".join(title.split())[:160]
    subject, event = EVENTS[item["action"]]
    summary = f"{LABELS[item['target_kind']]}「{title}」の{event}。"
    link = settings.frontend_public_url.rstrip("/") + (
        "/shared/notifications" if external else "/notifications"
    )
    text = (
        f"{summary}\n通知画面で対象と次の操作を確認してください。\n{link}\n\n"
        "このメールから直接承認はできません。ログインと追加認証が必要です。"
    )
    html = (
        f'<p>{escape(summary)}</p><p><a href="{escape(link, quote=True)}">'
        "通知画面で確認する</a></p><p>このメールから直接承認はできません。"
        "ログインと追加認証が必要です。</p>"
    )
    return OutgoingEmail(
        to=user.email,
        subject=f"[Syncnesto] {subject}",
        text=text,
        html=html,
        idempotency_key=f"approval-mail/{delivery_id}",
    )


def content_digest(message: OutgoingEmail) -> str:
    """再送時のtemplate/概要/遷移先の変更を検出する。"""
    return hashlib.sha256(
        json.dumps(
            [message.subject, message.text, message.html], ensure_ascii=False
        ).encode()
    ).hexdigest()
