"""承認保存transaction内の配送待ちと、LOCALだけの配送worker。"""

from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.notification_email import NotificationEmailAttempt as Attempt
from app.models.notification_email import NotificationEmailDelivery as Delivery
from app.models.notification_email import NotificationEmailPreference as Preference
from app.services.notification_email_content import (
    content_digest,
    eligible_notification,
    render_email,
)
from app.services.notification_email_preferences import local_mail_enabled
from app.services.notification_email_transport import (
    LocalApprovalMailTransport,
    MailOutcome,
)

WORKER_LOCK = 1398362934


def enqueue_notification_email(db, notification_id):
    """今回INSERTできた通知だけを同じtransactionで保存する。"""
    if not local_mail_enabled():
        return
    value = eligible_notification(db, notification_id)
    if value is None:
        return
    item, user, preference, external = value
    identity = uuid4()
    message = render_email(item, user, external, identity)
    db.execute(
        insert(Delivery)
        .values(
            id=identity,
            notification_id=notification_id,
            preference_version=preference.version,
            email_digest=preference.email_digest,
            template_version=1,
            content_digest=content_digest(message),
            state="pending",
            version=1,
            attempt_count=0,
            expires_at=item["created_at"] + timedelta(hours=24),
        )
        .on_conflict_do_nothing(index_elements=["notification_id"])
    )


class ApprovalMailWorker:
    """資格を保持する接続と、開始/結果を確定する接続を分ける。"""

    def __init__(self, engine, transport=None):
        """配送先のDBは設定とは独立に再検査する。"""
        self.engine = engine
        self.sessions = sessionmaker(bind=engine, expire_on_commit=False)
        self.transport = transport or LocalApprovalMailTransport()

    def local_only(self):
        """公開・デモ・遠隔DBを操作しない。"""
        from app.core.config import settings

        if (
            settings.is_public_environment
            or settings.demo_mode
            or self.engine.url.host not in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("local_database_required")

    @contextmanager
    def gate(self):
        """複数起動と手動解決を同時に走らせない。"""
        self.local_only()
        with self.engine.connect() as connection:
            acquired = connection.scalar(
                text("SELECT pg_try_advisory_lock(:key,0)"), {"key": WORKER_LOCK}
            )
            try:
                yield bool(acquired)
            finally:
                if acquired:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:key,0)"), {"key": WORKER_LOCK}
                    )

    def record(
        self,
        db,
        row,
        kind,
        outcome=None,
        code=None,
        message_id=None,
        reason=None,
        operator="worker",
    ):
        """元の試行記録を更新せず、安全なcodeを追記する。"""
        db.execute(
            insert(Attempt)
            .values(
                id=uuid4(),
                delivery_id=row.id,
                attempt_id=row.claim_token,
                record_kind=kind,
                outcome=outcome,
                safe_code=code,
                provider_message_id=message_id,
                reason=reason,
                operator_kind=operator,
            )
            .on_conflict_do_nothing(index_elements=["attempt_id", "record_kind"])
        )

    def recover_and_claim(self):
        """開始前のleaseだけ再取得し、開始済みの終了不明は隔離する。"""
        with self.sessions.begin() as db:
            now = db.execute(select(func.clock_timestamp())).scalar_one()
            expired = db.scalars(
                select(Delivery)
                .where(
                    Delivery.state.in_(["leased", "sending"]),
                    Delivery.lease_until <= now,
                )
                .order_by(Delivery.id)
                .limit(20)
                .with_for_update(skip_locked=True)
            ).all()
            for row in expired:
                if row.state == "sending":
                    row.state = "unknown"
                    self.record(db, row, "completed", "unknown", "worker_interrupted")
                else:
                    row.state, row.claim_token, row.lease_until = "pending", None, None
                row.version += 1
            row = db.scalar(
                select(Delivery)
                .where(
                    Delivery.state.in_(["pending", "retry_wait"]),
                    Delivery.next_attempt_at <= now,
                )
                .order_by(Delivery.next_attempt_at, Delivery.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            row.state, row.claim_token = "leased", uuid4()
            row.lease_until = now + timedelta(seconds=30)
            row.version += 1
            db.flush()
            return row

    def lock_internal_permissions(self, db, project_id, user_id):
        """既存role書換えを待たせ、最終SELECTの権限集合を保護する。"""
        tables = Base.metadata.tables
        members = tables["project_members"]
        roles = list(
            db.scalars(
                select(members.c.role_id)
                .where(
                    members.c.project_id == project_id,
                    members.c.user_id == user_id,
                    members.c.deleted_at.is_(None),
                )
                .order_by(members.c.id)
                .with_for_update(read=True)
            )
        )
        role = tables["roles"]
        list(
            db.execute(
                select(role.c.id)
                .where(role.c.id.in_(roles))
                .order_by(role.c.id)
                .with_for_update(read=True)
            )
        )
        rp, perm = tables["role_permissions"], tables["permissions"]
        permission_ids = list(
            db.scalars(select(rp.c.permission_id).where(rp.c.role_id.in_(roles)))
        )
        list(
            db.execute(
                select(perm.c.id)
                .where(perm.c.id.in_(permission_ids))
                .order_by(perm.c.id)
                .with_for_update(read=True)
            )
        )
        list(
            db.execute(
                select(rp)
                .where(rp.c.role_id.in_(roles))
                .order_by(rp.c.role_id, rp.c.permission_id)
                .with_for_update(read=True)
            )
        )

    def finish(self, identity, token, result):
        """受付済みを再配送せず、失敗と結果不明を分けて確定する。"""
        with self.sessions.begin() as db:
            row = db.scalar(
                select(Delivery)
                .where(
                    Delivery.id == identity,
                    Delivery.claim_token == token,
                    Delivery.state.in_(["leased", "sending"]),
                )
                .with_for_update()
            )
            if row is None:
                return
            now = db.execute(select(func.clock_timestamp())).scalar_one()
            if row.state == "leased":
                row.attempt_count += 1
            state = result.outcome
            if state == "temporary":
                if now >= row.expires_at:
                    state = "suppressed"
                elif row.attempt_count <= 3:
                    state = "retry_wait"
                    row.next_attempt_at = now + timedelta(
                        seconds=[60, 300, 1800][row.attempt_count - 1]
                    )
                else:
                    state = "failed"
            row.state, row.version = state, row.version + 1
            self.record(
                db, row, "completed", result.outcome, result.code, result.message_id
            )

    def deliver(self, claimed):
        """待機後の再認可と送信の間で停止を追い越さない。"""
        result = MailOutcome("temporary", "qualification_unavailable")
        try:
            with self.sessions.begin() as guard:
                guard.execute(text("SET LOCAL statement_timeout='2000ms'"))
                guard.execute(text("SET LOCAL lock_timeout='1000ms'"))
                guard.execute(text("SELECT pg_advisory_xact_lock_shared(1398362932,0)"))
                n = Base.metadata.tables["notifications"]
                source = guard.execute(
                    select(n.c.project_id, n.c.recipient_user_id).where(
                        n.c.id == claimed.notification_id
                    )
                ).first()
                if source is None or source.project_id is None:
                    result = MailOutcome("suppressed", "notification_unavailable")
                    self.finish(claimed.id, claimed.claim_token, result)
                    return result
                guard.execute(
                    text("SELECT publication_lock(:project,true)"),
                    {"project": source.project_id},
                )
                self.lock_internal_permissions(
                    guard, source.project_id, source.recipient_user_id
                )
                guard.scalar(
                    select(Preference)
                    .where(Preference.user_id == source.recipient_user_id)
                    .with_for_update(read=True)
                )
                value = eligible_notification(guard, claimed.notification_id)
                now = guard.scalar(select(func.clock_timestamp()))
                if value is None or now >= claimed.expires_at:
                    result = MailOutcome("suppressed", "qualification_or_expiry")
                else:
                    item, user, pref, external = value
                    message = render_email(item, user, external, claimed.id)
                    if (
                        pref.version != claimed.preference_version
                        or pref.email_digest != claimed.email_digest
                        or claimed.template_version != 1
                        or content_digest(message) != claimed.content_digest
                    ):
                        result = MailOutcome(
                            "suppressed", "identity_or_content_changed"
                        )
                    else:
                        expiry = (
                            min(claimed.expires_at, item["effective_expiry"])
                            if external
                            else claimed.expires_at
                        )
                        with self.sessions.begin() as control:
                            row = control.scalar(
                                select(Delivery)
                                .where(
                                    Delivery.id == claimed.id,
                                    Delivery.claim_token == claimed.claim_token,
                                    Delivery.state == "leased",
                                    Delivery.lease_until > func.clock_timestamp(),
                                )
                                .with_for_update()
                            )
                            if row is None:
                                return MailOutcome("suppressed", "claim_lost")
                            row.state, row.version = "sending", row.version + 1
                            row.attempt_count += 1
                            self.record(control, row, "started")
                        result = self.transport.send(message, guard, expiry)
                self.finish(claimed.id, claimed.claim_token, result)
                return result
        except Exception:
            # 耐久化した開始記録より前の障害だけを安全に再試行する。
            with self.sessions() as control:
                current = control.get(Delivery, claimed.id)
                started = current is None or current.state != "leased"
            result = MailOutcome(
                "unknown" if started else "temporary", "worker_or_database_unavailable"
            )
            self.finish(claimed.id, claimed.claim_token, result)
            return result

    def run_once(self):
        """全体をsingletonにし、claimだけの多重送信を避ける。"""
        with self.gate() as acquired:
            if not acquired:
                return {"busy": 1}
            if not local_mail_enabled():
                return {"disabled": 1}
            claimed = self.recover_and_claim()
            if claimed is None:
                return {"idle": 1}
            result = self.deliver(claimed)
            return {result.outcome: 1}

    def status(self):
        """メールや対象本文を表示しない。"""
        self.local_only()
        with self.sessions() as db:
            return {
                state: count
                for state, count in db.execute(
                    select(Delivery.state, func.count()).group_by(Delivery.state)
                ).all()
            }

    def resolve(
        self, identity: UUID, version: int, accepted: bool, reason: str, evidence: dict
    ):
        """終了済みの結果不明だけを明示的な根拠で解決する。"""
        import hashlib
        import json
        from email.parser import BytesParser
        from urllib.parse import quote

        import httpx

        if not 1 <= len(reason.strip()) <= 400:
            raise ValueError("reason_and_evidence_required")
        with self.gate() as acquired:
            if not acquired:
                raise ValueError("worker_busy")
            with self.sessions.begin() as db:
                row = db.scalar(
                    select(Delivery).where(Delivery.id == identity).with_for_update()
                )
                now = db.execute(select(func.clock_timestamp())).scalar_one()
                if (
                    row is None
                    or row.state != "unknown"
                    or row.version != version
                    or row.lease_until is None
                    or row.lease_until > now
                ):
                    raise ValueError("unknown_finished_version_required")
                mail_digest = hashlib.sha256(
                    f"approval-mail/{row.id}".encode()
                ).hexdigest()
                expected_id = f"<{mail_digest}@example.com>"
                if (
                    evidence.get("attempt_id") != str(row.claim_token)
                    or evidence.get("message_id") != expected_id
                ):
                    raise ValueError("evidence_attempt_mismatch")
                if accepted:
                    mailpit_id = quote(str(evidence.get("mailpit_id", "")), safe="")
                    response = httpx.get(
                        f"http://127.0.0.1:8025/api/v1/message/{mailpit_id}/raw",
                        timeout=3,
                        follow_redirects=False,
                    )
                    if (
                        response.status_code != 200
                        or len(response.content) > 65536
                        or BytesParser().parsebytes(response.content)["Message-ID"]
                        != expected_id
                    ):
                        raise ValueError("mailpit_receipt_not_verified")
                    row.state = "accepted"
                else:
                    if (
                        evidence.get("basis") != "smtp_rejection"
                        or not isinstance(evidence.get("smtp_code"), int)
                        or not 400 <= evidence["smtp_code"] < 600
                        or evidence.get("reply_received") is not True
                    ):
                        raise ValueError("positive_nonacceptance_evidence_required")
                    row.state = (
                        "suppressed"
                        if now >= row.expires_at
                        else "failed"
                        if evidence["smtp_code"] >= 500 or row.attempt_count >= 4
                        else "retry_wait"
                    )
                    row.next_attempt_at = now + timedelta(
                        seconds=[60, 300, 1800][row.attempt_count - 1]
                        if row.state == "retry_wait"
                        else 0
                    )
                row.version += 1
                digest = hashlib.sha256(
                    json.dumps(evidence, sort_keys=True).encode()
                ).hexdigest()
                self.record(
                    db,
                    row,
                    "resolution",
                    row.state,
                    "operator_confirmed",
                    reason=f"{reason}\nevidence_sha256:{digest}",
                    operator="local_operator",
                )
