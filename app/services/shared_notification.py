"""現在の公開世代と実MFAを必須にした共有通知・既読管理。"""

from contextlib import contextmanager

from sqlalchemy import func, select, text, update

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.repositories.fixed_approval_notification import (
    FixedApprovalNotificationRepository,
)
from app.schemas.fixed_approval_notification import (
    SharedNotificationPage,
    SharedNotificationRead,
)
from app.services.publication_access_context import retry_publication_operation
from app.services.shared_fixed_approval import SharedFixedApprovalService


class SharedNotificationService:
    """一覧・件数・既読へ同じ認可済み集合を適用する。"""

    def __init__(self):
        """既存の本人/公開境界を再利用する。"""
        self.repository = FixedApprovalNotificationRepository()
        self.approvals = SharedFixedApprovalService()

    @contextmanager
    def access(self, original, user):
        """Project順に境界を取り、待機後に再認可して件数も保護する。"""
        if not settings.fixed_approval_notifications_enabled:
            raise NotFoundError()
        with self.approvals.shared.context.transaction(original, user) as (db, actor):
            query = self.repository.shared(actor.id)
            projects = sorted(
                set(db.scalars(query.with_only_columns(self.repository.r.c.project_id)))
            )
            for project in projects:
                db.execute(
                    text("SELECT publication_lock(:project,true)"), {"project": project}
                )
            self.approvals.shared.context.verify(db, original, actor, internal=False)
            self.approvals.mfa(db, actor)
            yield (
                db,
                actor,
                self.repository.shared(actor.id).where(
                    self.repository.r.c.project_id.in_(projects)
                ),
            )

    def read(self, db, original, actor, item):
        """現在の親と資格を再検査し、公開済みの依頼名だけを投影する。"""
        public = item["public_context"]
        revision = self.approvals.shared.revision(
            db, actor, {**item, "id": item["publication_id"]}
        )
        return SharedNotificationRead(
            id=item["event_id"],
            type=item["type"],
            approval_id=item["approval_id"],
            publication_id=item["publication_id"],
            target_kind=item["target_kind"],
            title=public["title"],
            requester_name=public["requester_name"],
            action=item["action"],
            current_status=item["current_status"],
            can_decide=item["current_status"] == "requested"
            and settings.guest_fixed_approvals_enabled,
            is_read=item["read_at"] is not None,
            read_at=item["read_at"],
            created_at=item["created_at"],
            expires_at=item["effective_expiry"],
            authorization_revision=revision,
        )

    @retry_publication_operation
    def listing(self, original, user, page, page_size, unread_only):
        """権限条件の後でtotalとpageを計算する。"""
        with self.access(original, user) as (db, actor, query):
            if unread_only:
                query = query.where(self.repository.n.c.read_at.is_(None))
            total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
            items = (
                db.execute(
                    query.order_by(
                        self.repository.n.c.created_at.desc(),
                        self.repository.e.c.id.desc(),
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
                .mappings()
                .all()
            )
            return SharedNotificationPage(
                items=[self.read(db, original, actor, item) for item in items],
                total=total,
                page=page,
                page_size=page_size,
            )

    @retry_publication_operation
    def unread_count(self, original, user):
        """本文と同じ集合の未読数だけを返す。"""
        with self.access(original, user) as (db, _, query):
            query = query.where(self.repository.n.c.read_at.is_(None))
            return db.scalar(select(func.count()).select_from(query.subquery())) or 0

    @retry_publication_operation
    def mark_read(self, original, user, event_id=None):
        """現在読める自分宛てだけを更新し、既読で判断を進めない。"""
        with self.access(original, user) as (db, actor, query):
            if event_id is not None:
                query = query.where(self.repository.e.c.id == event_id)
                item = db.execute(query).mappings().first()
                if item is None:
                    raise NotFoundError()
                self.read(db, original, actor, item)
            n = self.repository.n
            changed = db.execute(
                update(n)
                .where(
                    n.c.id.in_(query.with_only_columns(n.c.id)), n.c.read_at.is_(None)
                )
                .values(read_at=func.clock_timestamp())
                .returning(n.c.id)
            )
            if event_id is None:
                return len(changed.scalars().all())
            item = db.execute(query).mappings().first()
            if item is None:
                raise NotFoundError()
            return self.read(db, original, actor, item)
