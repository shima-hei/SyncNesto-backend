"""同じ共有許可を現在持つ本人にだけ表示する通知。"""

from sqlalchemy import DateTime, String, and_, case, cast, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.db.base import Base
from app.repositories.artifact_publication_access import PublicationAccessRepository
from app.repositories.requirement_workflow import GUEST_DUE_ZONE

PUBLICATION_SHARED = "publication_shared"
SHARED_COMMENT_REPLY = "shared_comment_reply"
SHARED_COMMENT_MENTION = "shared_comment_mention"
REQUIREMENT_REVIEW_REQUESTED = "requirement_review_requested"
REQUIREMENT_APPROVAL_REQUESTED = "requirement_approval_requested"


class GuestActivityRepository:
    @property
    def notifications(self):
        return Base.metadata.tables["notifications"]

    def emit(
        self,
        db,
        *,
        tenant_id,
        project_id,
        actor_id,
        recipient_id,
        grant_id,
        publication_id,
        event_id,
        kind,
        thread_id=None,
    ):
        """現在の公開名を後から投影し、通知へ本文・名称を複製しない。"""
        if actor_id == recipient_id:
            return
        statement = insert(self.notifications).values(
            tenant_id=tenant_id,
            project_id=project_id,
            recipient_user_id=recipient_id,
            type=kind,
            actor_user_id=actor_id,
            target_type="shared_publication",
            target_id=str(event_id),
            event_key=f"{kind}:{event_id}",
            snapshot={},
            context={
                "grant_id": str(grant_id),
                "publication_id": str(publication_id),
                "thread_id": str(thread_id) if thread_id else None,
            },
        )
        db.execute(
            statement.on_conflict_do_nothing(
                index_elements=["event_key", "recipient_user_id"]
            )
        )

    def visible(self, user_id):
        """付与IDの一致を要求し、再参加や再付与で旧通知を復活させない。"""
        access = PublicationAccessRepository().shared_query(user_id)
        cols = access.selected_columns
        allowed = access.with_only_columns(
            cols.id,
            cols.title,
            cols.project_id,
            cols.grant_id,
            cols.effective_expiry,
        ).subquery()
        t = self.notifications
        request = Base.metadata.tables["requirement_workflow_requests"]
        binding = Base.metadata.tables["requirement_workflow_guest_bindings"]
        decision_notice = t.c.type.in_(
            [REQUIREMENT_REVIEW_REQUESTED, REQUIREMENT_APPROVAL_REQUESTED]
        )
        response_end = func.timezone(
            GUEST_DUE_ZONE.key, cast(request.c.due_on + 1, DateTime())
        )
        expiry = func.least(
            allowed.c.effective_expiry,
            case(
                (request.c.status == "requested", response_end),
                else_=allowed.c.effective_expiry,
            ),
        )
        return (
            select(
                t.c.target_id.label("id"),
                t.c.type.label("kind"),
                t.c.created_at,
                t.c.read_at,
                t.c.context["thread_id"].astext.label("thread_id"),
                allowed.c.id.label("publication_id"),
                allowed.c.project_id,
                allowed.c.title,
                expiry.label("expires_at"),
            )
            .join(
                allowed,
                and_(
                    t.c.project_id == allowed.c.project_id,
                    t.c.context["publication_id"].astext == cast(allowed.c.id, String),
                    t.c.context["grant_id"].astext == cast(allowed.c.grant_id, String),
                ),
            )
            .outerjoin(
                request,
                and_(
                    decision_notice,
                    request.c.project_id == allowed.c.project_id,
                    cast(request.c.id, String) == t.c.target_id,
                ),
            )
            .where(
                t.c.recipient_user_id == user_id,
                t.c.target_type == "shared_publication",
                expiry > func.clock_timestamp(),
                or_(
                    t.c.type.in_(
                        [
                            PUBLICATION_SHARED,
                            SHARED_COMMENT_REPLY,
                            SHARED_COMMENT_MENTION,
                        ]
                    ),
                    and_(
                        request.c.status != "cancelled",
                        or_(
                            and_(
                                t.c.type == REQUIREMENT_REVIEW_REQUESTED,
                                request.c.kind == "review",
                            ),
                            and_(
                                t.c.type == REQUIREMENT_APPROVAL_REQUESTED,
                                request.c.kind == "formal_approval",
                            ),
                        ),
                        exists().where(
                            binding.c.request_id == request.c.id,
                            binding.c.user_id == user_id,
                            binding.c.project_id == allowed.c.project_id,
                            binding.c.publication_id == allowed.c.id,
                            binding.c.grant_id == allowed.c.grant_id,
                        ),
                    ),
                ),
            )
        )

    @staticmethod
    def counts(db, query):
        allowed = query.subquery()
        return db.execute(
            select(func.count(), func.count().filter(allowed.c.read_at.is_(None)))
        ).one()
