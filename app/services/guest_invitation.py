"""招待・メール確認・本人受諾を現在の資格で原子的に確定する。"""

import hashlib
import hmac
import json
import secrets
from contextlib import contextmanager
from datetime import timedelta
from html import escape
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages as messages
from app.core.config import settings
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    DuplicateResourceError,
    EmailUnavailableError,
    ForbiddenError,
    LocalEmailCaptureError,
    MfaRequiredError,
    NotFoundError,
    VersionConflictError,
)
from app.core.security import get_password_hash
from app.db.base import Base
from app.models.artifact_publication_access import ProjectGuestParticipation
from app.models.review_account import ReviewAccountGrant
from app.models.user import User
from app.repositories.guest_invitation import GuestInvitationRepository
from app.schemas.guest_invitation import (
    InvitationEventRead,
    InvitationEvents,
    InvitationOperation,
    InvitationPage,
    InvitationRead,
    InvitationRecipientRead,
)
from app.services.audit_log import AuditLogService
from app.services.email import EmailService, OutgoingEmail
from app.services.publication_access_context import (
    PublicationAccessContext,
    retry_publication_operation,
)


def secret_digest(value):
    """ランダムな確認secretの一方向hash。"""
    return hashlib.sha256(value.encode()).hexdigest()


def input_digest(kind, data):
    """初回パスワードを推測検証できる単純hashとして保存しない。"""
    return hmac.new(
        settings.secret_key.encode(),
        json.dumps(
            {"kind": kind, "data": data.model_dump(mode="json")}, sort_keys=True
        ).encode(),
        hashlib.sha256,
    ).hexdigest()


class GuestInvitationService:
    """新しい限定処理以外の所属・権限・認証情報には触れない。"""

    def __init__(self):
        """既存の境界ロック・再認可と配送を使う。"""
        self.repository = GuestInvitationRepository()
        self.context = PublicationAccessContext()
        self.email = EmailService()

    @staticmethod
    def enabled():
        """停止時は新操作を拒否する。readerは維持する。"""
        if not settings.guest_invitations_enabled:
            raise ForbiddenError()

    @staticmethod
    def state(row, now):
        """定期処理を待たず期限切れを表示する。"""
        return (
            "expired"
            if row["status"] == "pending" and row["expires_at"] <= now
            else row["status"]
        )

    def read(self, row, now):
        """管理者への応答にも登録済みかどうかを返さない。"""
        return InvitationRead(
            id=row["id"],
            email=row["recipient_email"],
            display_label=row["display_label"],
            state=self.state(row, now),
            version=row["version"],
            delivery_state=row["delivery_state"],
            expires_at=row["expires_at"],
            participation_expires_at=row["participation_expires_at"],
            created_at=row["created_at"],
        )

    def require_row(self, db, identity, project_id=None, *, lock=False):
        """同一Projectの対象を取得する。"""
        row = self.repository.invitation(db, identity, project_id=project_id, lock=lock)
        if row is None:
            raise NotFoundError()
        return row

    @staticmethod
    def version(row, data):
        """競合応答へ宛先や本文を混ぜない。"""
        if row["version"] != data.version:
            raise VersionConflictError(
                {"version": row["version"], "state": row["status"]}
            )

    def record(
        self, db, row, kind, actor, data, *, email_actor=False, root=True, reason=None
    ):
        """業務経緯と監査を同じtransactionに記録する。"""
        result = InvitationOperation(
            id=row["id"],
            state=row["status"],
            version=row["version"],
            delivery_state=row["delivery_state"],
        )
        self.repository.add(
            db,
            self.repository.events,
            id=uuid4(),
            project_id=row["project_id"],
            invitation_id=row["id"],
            kind=kind,
            actor_id=actor.id if actor else None,
            actor_kind="email_recipient"
            if email_actor
            else "user"
            if actor
            else "delivery",
            operation_id=data.request_id,
            request_digest=input_digest(kind, data),
            is_root=root,
            version=row["version"],
            state=row["status"],
            reason=reason,
            result=result.model_dump(mode="json"),
        )
        AuditLogService().record(
            db,
            event_type=f"guest_invitation.{kind}",
            tenant_id=db.scalar(
                select(Base.metadata.tables["projects"].c.tenant_id).where(
                    Base.metadata.tables["projects"].c.id == row["project_id"]
                )
            ),
            actor_user_id=actor.id if actor else None,
            project_id=row["project_id"],
            resource_type="guest_invitation",
            metadata={
                "invitation_id": str(row["id"]),
                "operation_id": str(data.request_id),
            },
            commit=False,
        )
        return result

    def replay(self, db, row, kind, data, actor_id=None):
        """異操作/別操作者による同じIDの流用は拒否する。"""
        previous = self.repository.previous(db, row["id"], data.request_id)
        if previous is None:
            return None
        if (
            previous["request_digest"] != input_digest(kind, data)
            or previous["actor_id"] != actor_id
        ):
            raise DuplicateResourceError()
        return InvitationOperation.model_validate(previous["result"])

    def management(self, original, user, project_id, *, write=False, remove=False):
        """既存の組織管理/Project管理の経路を新操作でも再確認する。"""
        return self.context.transaction(
            original,
            user,
            project_id,
            write=write,
            qualifying=write,
            management="project:remove_member" if remove else "project:invite_member",
        )

    def listing(self, original, user, project_id, page, page_size):
        """招待の管理メタデータだけを返す。"""
        with self.management(original, user, project_id) as (db, _):
            rows, total = self.repository.page(db, project_id, page, page_size)
            now = db.execute(select(func.clock_timestamp())).scalar_one()
            return InvitationPage(
                items=[self.read(row, now) for row in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

    def events(self, original, user, project_id, identity, page, page_size):
        """履歴内の現在氏名だけを補助表示し、証拠のactorを変えない。"""
        with self.management(original, user, project_id) as (db, _):
            self.require_row(db, identity, project_id)
            rows, total = self.repository.page(
                db, project_id, page, page_size, identity=identity
            )
            ids = {row["actor_id"] for row in rows if row["actor_id"] is not None}
            names = (
                {
                    u.id: u.name
                    for u in db.execute(
                        select(User.__table__.c.id, User.__table__.c.name).where(
                            User.__table__.c.id.in_(ids),
                            User.__table__.c.deleted_at.is_(None),
                        )
                    )
                }
                if ids
                else {}
            )
            return InvitationEvents(
                items=[
                    InvitationEventRead(
                        **{
                            key: row[key]
                            for key in (
                                "id",
                                "kind",
                                "actor_kind",
                                "occurred_at",
                                "state",
                                "reason",
                            )
                        },
                        actor_name=names.get(row["actor_id"]),
                    )
                    for row in rows
                ],
                total=total,
                page=page,
                page_size=page_size,
            )

    @retry_publication_operation
    def prepare(self, original, user, project_id, data, identity=None):
        """送信より先に招待を保存し、再送で旧世代を失効する。"""
        self.enabled()
        table = self.repository.invitations
        with self.management(original, user, project_id, write=True) as (db, actor):
            now = db.execute(select(func.clock_timestamp())).scalar_one()
            if identity is None:
                previous = (
                    db.execute(
                        select(table).where(
                            table.c.project_id == project_id,
                            table.c.created_by == actor.id,
                            table.c.request_id == data.request_id,
                        )
                    )
                    .mappings()
                    .first()
                )
                if previous is not None:
                    if previous["request_digest"] != input_digest("created", data):
                        raise DuplicateResourceError()
                    return self.read(previous, now), None
                email = str(data.email)
                if not self.email.is_local_capture(email):
                    raise ForbiddenError()
                if data.participation_expires_at <= now:
                    raise BadRequestError(messages.GUEST_INVITATION_FUTURE_EXPIRY)
                pending = (
                    db.execute(
                        select(table)
                        .where(
                            table.c.project_id == project_id,
                            func.lower(table.c.recipient_email) == email.lower(),
                            table.c.status == "pending",
                        )
                        .with_for_update()
                    )
                    .mappings()
                    .first()
                )
                if pending:
                    if self.state(pending, now) != "expired":
                        raise ConflictError(messages.GUEST_INVITATION_PENDING)
                    self.repository.update(
                        db,
                        table,
                        pending["id"],
                        status="expired",
                        version=pending["version"] + 1,
                    )
                    self.record(
                        db, self.require_row(db, pending["id"]), "expired", actor, data
                    )
                identities = self.repository.identities(db, email)
                target = (
                    identities[0]
                    if len(identities) == 1 and identities[0].email == email
                    else None
                )
                token = secrets.token_urlsafe(32)
                identity = uuid4()
                self.repository.add(
                    db,
                    table,
                    id=identity,
                    project_id=project_id,
                    recipient_email=email,
                    recipient_id=target.id if target else None,
                    display_label=data.display_label,
                    created_by=actor.id,
                    expires_at=min(
                        now + timedelta(days=7), data.participation_expires_at
                    ),
                    participation_expires_at=data.participation_expires_at,
                    status="pending",
                    version=1,
                    token_generation=1,
                    token_hash=secret_digest(token),
                    delivery_state="sending",
                    request_id=data.request_id,
                    request_digest=input_digest("created", data),
                )
                row = self.require_row(db, identity)
                self.record(db, row, "created", actor, data)
            else:
                row = self.require_row(db, identity, project_id, lock=True)
                previous = self.replay(db, row, "resent", data, actor.id)
                if previous:
                    return self.read(row, now), None
                self.version(row, data)
                self.inviter(db, row)
                if row["created_by"] != actor.id or self.state(row, now) != "pending":
                    raise ConflictError(messages.GUEST_INVITATION_RECREATE)
                if not self.email.is_local_capture(row["recipient_email"]):
                    raise ForbiddenError()
                token = secrets.token_urlsafe(32)
                self.repository.update(
                    db,
                    table,
                    identity,
                    token_hash=secret_digest(token),
                    token_generation=row["token_generation"] + 1,
                    version=row["version"] + 1,
                    delivery_state="sending",
                    sent_at=None,
                    provider_message_id=None,
                )
                row = self.require_row(db, identity)
                self.record(db, row, "resent", actor, data)
            return self.read(row, now), token

    def send(self, original, user, project_id, identity, token, data):
        """平文tokenはmemoryとメールだけに使い、結果不明を自動retryしない。"""
        with self.management(original, user, project_id) as (db, _):
            row = dict(self.require_row(db, identity, project_id))
            organization, project = self.project(db, project_id)
        origin = settings.frontend_public_url.rstrip("/")
        link = f"{origin}/guest-invitations/{identity}#token={token}"
        body = (
            f"{organization} / {project} への参加のご案内です。\n{link}\n\n"
            f"招待期限: {row['expires_at'].isoformat()}\n"
            f"参加期限: {row['participation_expires_at'].isoformat()}\n"
            "参加と資料ごとの公開・承認権限は別です。"
            "心当たりがなければ操作しないでください。"
        )
        message_id = None
        try:
            message_id = self.email.send(
                OutgoingEmail(
                    to=row["recipient_email"],
                    subject="[Syncnesto LOCAL] プロジェクトへの招待",
                    text=body,
                    html=(
                        f"<p>{escape(organization)} / {escape(project)}"
                        " への招待です。</p>"
                        f'<p><a href="{escape(link, quote=True)}">'
                        "招待内容を確認する</a></p>"
                        "<p>参加と資料公開・承認は別です。</p>"
                    ),
                    idempotency_key=f"guest-invitation/{identity}/{row['token_generation']}",
                )
            )
            delivery = "sent"
        except LocalEmailCaptureError as error:
            delivery = "unknown" if error.uncertain else "failed"
        except EmailUnavailableError:
            delivery = "unknown"
        with self.management(original, user, project_id, write=True) as (db, actor):
            current = self.require_row(db, identity, project_id, lock=True)
            if (
                current["status"] == "pending"
                and current["token_generation"] == row["token_generation"]
            ):
                self.repository.update(
                    db,
                    self.repository.invitations,
                    identity,
                    version=current["version"] + 1,
                    delivery_state=delivery,
                    sent_at=db.execute(select(func.clock_timestamp())).scalar_one()
                    if message_id
                    else None,
                    provider_message_id=message_id,
                )
                current = self.require_row(db, identity)
                self.record(
                    db, current, f"delivery_{delivery}", actor, data, root=False
                )
            return self.read(
                current, db.execute(select(func.clock_timestamp())).scalar_one()
            )

    @retry_publication_operation
    def revoke(self, original, user, project_id, identity, data):
        """受諾済み参加と混同せず、pending招待だけを取り消す。"""
        self.enabled()
        with self.management(original, user, project_id, write=True, remove=True) as (
            db,
            actor,
        ):
            row = self.require_row(db, identity, project_id, lock=True)
            previous = self.replay(db, row, "revoked", data, actor.id)
            if previous:
                return previous
            self.version(row, data)
            if row["status"] != "pending" or not data.reason.strip():
                raise ConflictError()
            self.repository.update(
                db,
                self.repository.invitations,
                identity,
                status="revoked",
                version=row["version"] + 1,
            )
            return self.record(
                db,
                self.require_row(db, identity),
                "revoked",
                actor,
                data,
                reason=data.reason.strip(),
            )

    @staticmethod
    def project(db, project_id):
        """全Identityへの情報公開に流用しない最小の招待元表示。"""
        p, t = (Base.metadata.tables[name] for name in ("projects", "tenants"))
        result = db.execute(
            select(t.c.name, p.c.name)
            .join(p, p.c.tenant_id == t.c.id)
            .where(
                p.c.id == project_id, p.c.deleted_at.is_(None), t.c.status == "active"
            )
        ).first()
        if result is None:
            raise NotFoundError()
        return result[0], result[1]

    def inviter(self, db, row):
        """申請時だけでなく受諾時の招待者の管理権限も確認する。"""
        self.project(db, row["project_id"])
        actor = db.get(User, row["created_by"], populate_existing=True)
        if (
            actor is None
            or not actor.is_active
            or actor.deleted_at is not None
            or actor.user_type != "internal"
            or actor.password_change_required
            or db.get(ReviewAccountGrant, actor.id)
        ):
            raise NotFoundError()
        p = Base.metadata.tables["projects"]
        tenant_id = db.scalar(select(p.c.tenant_id).where(p.c.id == row["project_id"]))
        old = dict(db.info)
        try:
            db.info.update(tenant_id=tenant_id, tenant_user_id=actor.id)
            self.context.management(
                db, actor, row["project_id"], "project:invite_member"
            )
        except ForbiddenError:
            raise NotFoundError() from None
        finally:
            db.info.clear()
            db.info.update(old)

    @contextmanager
    def recipient_transaction(self, original, identity, user=None):
        """新3表に限定し、通常tenant contextを外部へ開放しない。"""
        if (
            original.info.get("data_realm") == "demo"
            or original.info.get("demo_id")
            or original.info.get("mcp_connection_id")
        ):
            raise ForbiddenError()
        with (
            original.get_bind()
            .engine.connect()
            .execution_options(isolation_level="READ COMMITTED") as connection,
            Session(bind=connection) as db,
            db.begin(),
        ):
            initial = self.require_row(db, identity)
            ids = {initial["created_by"]}
            if initial["recipient_id"] is not None:
                ids.add(initial["recipient_id"])
            if user:
                ids.add(user.id)
            db.execute(
                select(User.id)
                .where(User.id.in_(ids))
                .order_by(User.id)
                .with_for_update(read=True, key_share=True)
            )
            db.execute(text("SELECT pg_advisory_xact_lock(1398362932,0)"))
            db.execute(
                text("SELECT publication_lock(:project)"),
                {"project": initial["project_id"]},
            )
            row = self.require_row(db, identity, lock=True)
            self.inviter(db, row)
            yield db, row

    def checked(self, db, row, proof, *, terminal=False):
        """元招待・確認世代・期限のどれかが失効すれば拒否する。"""
        if not proof or len(proof) > 200:
            raise NotFoundError()
        check = self.repository.check(db, row["id"], secret_digest(proof))
        now = db.execute(select(func.clock_timestamp())).scalar_one()
        if (
            check is None
            or check["token_generation"] != row["token_generation"]
            or check["expires_at"] <= now
            or row["expires_at"] <= now
            or (row["status"] != "pending" and not terminal)
        ):
            raise NotFoundError()
        return check

    def target(self, db, row):
        """類似メールの曖昧な紐付けや無効Identityの再作成をしない。"""
        candidates = self.repository.identities(db, row["recipient_email"])
        if not candidates:
            if row["recipient_id"] is not None:
                raise NotFoundError()
            return None
        if len(candidates) != 1 or candidates[0].email != row["recipient_email"]:
            raise NotFoundError()
        person = candidates[0]
        p, m = (Base.metadata.tables[name] for name in ("projects", "tenant_members"))
        internal = db.scalar(
            select(m.c.id)
            .join(p, p.c.tenant_id == m.c.tenant_id)
            .where(
                p.c.id == row["project_id"],
                m.c.user_id == person.id,
                m.c.status == "active",
            )
        )
        if (
            not person.is_active
            or person.deleted_at is not None
            or db.get(ReviewAccountGrant, person.id)
            or internal
            or row["recipient_id"] not in {None, person.id}
        ):
            raise NotFoundError()
        return person

    def recipient_read(self, db, row, user=None):
        """確認中の本人に必要な次の操作だけを返す。"""
        organization, project = self.project(db, row["project_id"])
        target = self.target(db, row)
        state = self.state(row, db.execute(select(func.clock_timestamp())).scalar_one())
        step = (
            "unavailable"
            if state not in {"pending", "accepted"}
            else "wrong_account"
            if user and (target is None or user.id != target.id)
            else "accepted"
            if state == "accepted"
            else "mfa"
            if user and not self.mfa_verified(db, user)
            else "accept"
            if user
            else "login"
            if target
            else "register"
        )

        return InvitationRecipientRead(
            id=row["id"],
            organization_name=organization,
            project_name=project,
            display_label=row["display_label"],
            state=state,
            version=row["version"],
            expires_at=row["expires_at"],
            participation_expires_at=row["participation_expires_at"],
            next_step=step,
            current_user_name=user.name if user else None,
        )

    @staticmethod
    def mfa_verified(db, user):
        """LOCAL全体の任意設定と独立して、新しい受諾に追加認証を要求する。"""
        session = db.info.get("auth_session")
        if (
            not session
            or user.mfa_recovery_pending
            or session.mfa_method not in {"totp", "passkey", "recovery_code"}
        ):
            return False
        if (
            session.mfa_verified_at
            and session.mfa_security_version == user.mfa_security_version
        ):
            return True
        # 既存の任意MFA再認証はsecurity_versionをsessionに複写しない。
        # 認証器/認証情報変更時は既存invalidateが全sessionを失効する。
        now = db.execute(select(func.clock_timestamp())).scalar_one()
        return bool(
            session.mfa_security_version is None
            and session.mfa_reauthenticated_at
            and now - timedelta(minutes=5) <= session.mfa_reauthenticated_at <= now
        )

    @retry_publication_operation
    def inspect(self, original, identity, token):
        """メールtokenは検証後に返さず、別乱数の確認Cookieを発行する。"""
        self.enabled()
        with self.recipient_transaction(original, identity) as (db, row):
            now = db.execute(select(func.clock_timestamp())).scalar_one()
            if (
                self.state(row, now) != "pending"
                or row["delivery_state"] not in {"sent", "unknown"}
                or not hmac.compare_digest(row["token_hash"], secret_digest(token))
            ):
                raise NotFoundError()
            self.target(db, row)
            proof = secrets.token_urlsafe(32)
            expiry = min(row["expires_at"], now + timedelta(seconds=1799))
            self.repository.add(
                db,
                self.repository.checks,
                id=uuid4(),
                project_id=row["project_id"],
                invitation_id=identity,
                token_generation=row["token_generation"],
                proof_hash=secret_digest(proof),
                expires_at=expiry,
            )
            return (
                self.recipient_read(db, row),
                proof,
                int((expiry - now).total_seconds()),
            )

    @retry_publication_operation
    def recipient(self, original, identity, proof, user):
        """参加/本文は作らず、短期確認を読んで案内する。"""
        with self.recipient_transaction(original, identity, user) as (db, row):
            self.checked(db, row, proof, terminal=True)
            if user:
                user = self.context.verify(db, original, user, internal=False)
            return self.recipient_read(db, row, user)

    @retry_publication_operation
    def register(self, original, identity, proof, data, user):
        """登録競合でも他のアカウントの認証情報を更新しない。"""
        self.enabled()
        with self.recipient_transaction(original, identity, user) as (db, row):
            check = self.checked(db, row, proof)
            if check["consumed_at"] or user or not data.name.strip():
                raise ConflictError()
            previous = self.repository.previous(db, identity, data.request_id)
            if previous:
                if previous["request_digest"] != input_digest("registered", data):
                    raise DuplicateResourceError()
                return self.recipient_read(db, row)
            self.version(row, data)
            if self.target(db, row) is not None:
                raise ConflictError(messages.GUEST_INVITATION_LOGIN)
            person = User(
                email=row["recipient_email"],
                name=data.name.strip(),
                hashed_password=get_password_hash(data.password),
                user_type="guest",
                created_by=row["created_by"],
            )
            db.add(person)
            try:
                db.flush()
            except IntegrityError:
                raise ConflictError(messages.GUEST_INVITATION_LOGIN) from None
            self.repository.update(
                db,
                self.repository.invitations,
                identity,
                recipient_id=person.id,
                version=row["version"] + 1,
            )
            row = self.require_row(db, identity)
            self.record(db, row, "registered", person, data, email_actor=True)
            return self.recipient_read(db, row)

    @retry_publication_operation
    def finish(self, original, identity, proof, data, user, *, decline=False):
        """受諾は通常MFA済みの指定本人だけ、辞退はメール確認として記録する。"""
        self.enabled()
        kind = "declined" if decline else "accepted"
        with self.recipient_transaction(original, identity, user) as (db, row):
            check = self.checked(db, row, proof, terminal=True)
            actor = (
                self.context.verify(db, original, user, internal=False)
                if user
                else None
            )
            person = self.target(db, row)
            if not decline and (
                actor is None or person is None or actor.id != person.id
            ):
                raise ForbiddenError()
            if not decline and not self.mfa_verified(db, actor):
                raise MfaRequiredError()
            if decline and actor and (person is None or actor.id != person.id):
                raise ForbiddenError()
            previous = self.replay(db, row, kind, data, actor.id if actor else None)
            if previous:
                return previous
            self.version(row, data)
            if row["status"] != "pending" or check["consumed_at"]:
                raise ConflictError()
            values = {"status": kind, "version": row["version"] + 1}
            if not decline:
                if actor is None or person is None:
                    raise ForbiddenError()
                part = ProjectGuestParticipation.__table__
                existing = db.scalar(
                    select(part.c.id).where(
                        part.c.project_id == row["project_id"],
                        part.c.user_id == actor.id,
                        part.c.status == "active",
                    )
                )
                if existing:
                    raise ConflictError(messages.GUEST_INVITATION_REVOKE_PARTICIPATION)
                generation = (
                    db.scalar(
                        select(func.coalesce(func.max(part.c.generation), 0)).where(
                            part.c.project_id == row["project_id"],
                            part.c.user_id == actor.id,
                        )
                    )
                    or 0
                ) + 1
                participation_id = uuid4()
                self.repository.add(
                    db,
                    part,
                    id=participation_id,
                    project_id=row["project_id"],
                    user_id=actor.id,
                    generation=generation,
                    display_label=person.name,
                    status="active",
                    expires_at=row["participation_expires_at"],
                    version=1,
                    created_by=actor.id,
                )
                values.update(
                    participation_id=participation_id,
                    accepted_by=actor.id,
                    recipient_id=actor.id,
                )
            self.repository.update(db, self.repository.invitations, identity, **values)
            self.repository.update(
                db,
                self.repository.checks,
                check["id"],
                consumed_at=db.execute(select(func.clock_timestamp())).scalar_one(),
            )
            return self.record(
                db,
                self.require_row(db, identity),
                kind,
                actor,
                data,
                email_actor=decline and actor is None,
            )
