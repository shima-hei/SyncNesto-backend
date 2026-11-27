"""固定版の共有に伴う本人限定の通知と既読。"""

from sqlalchemy import func, update

from app.core.exceptions import NotFoundError
from app.repositories.guest_activity import (
    SHARED_COMMENT_MENTION,
    SHARED_COMMENT_REPLY,
    GuestActivityRepository,
)
from app.schemas.guest_activity import GuestActivityPage, GuestActivityRead
from app.services.publication_access_context import (
    PublicationAccessContext,
    retry_publication_operation,
)


class GuestActivityService:
    def __init__(self):
        self.context = PublicationAccessContext()
        self.repository = GuestActivityRepository()

    @retry_publication_operation
    def listing(self, original, user, page, page_size, unread_only, comments_only):
        with self.context.transaction(original, user) as (db, actor):
            query = self.repository.visible(actor.id)
            cols = query.selected_columns
            if comments_only:
                query = query.where(
                    cols.kind.in_([SHARED_COMMENT_MENTION, SHARED_COMMENT_REPLY])
                )
            query = self.context.read_projects(
                db, original, actor, query, cols.project_id
            )
            total, unread = self.repository.counts(db, query)
            if unread_only:
                query = query.where(cols.read_at.is_(None))
            rows = db.execute(
                query.order_by(cols.created_at.desc(), cols.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).mappings()
            return GuestActivityPage(
                items=[GuestActivityRead.model_validate(row) for row in rows],
                total=unread if unread_only else total,
                unread=unread,
            )

    @retry_publication_operation
    def mark_read(self, original, user, identity):
        with self.context.transaction(original, user, write=True) as (db, actor):
            query = self.repository.visible(actor.id)
            query = query.where(query.selected_columns.id == str(identity))
            query = self.context.read_projects(
                db, original, actor, query, query.selected_columns.project_id
            )
            t = self.repository.notifications
            db.execute(
                update(t)
                .where(t.c.id.in_(query.with_only_columns(t.c.id)))
                .values(read_at=func.coalesce(t.c.read_at, func.clock_timestamp()))
            )
            row = db.execute(query).mappings().first()
            if row is None:
                raise NotFoundError()
            return GuestActivityRead.model_validate(row)
