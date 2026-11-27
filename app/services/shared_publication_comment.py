"""本人に許可された固定版の会話を共通処理で扱う。"""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.models.artifact_publication_access import SharedPublicationComment
from app.models.notification import NotificationType
from app.repositories.guest_activity import (
    SHARED_COMMENT_MENTION,
    SHARED_COMMENT_REPLY,
    GuestActivityRepository,
)
from app.repositories.shared_publication_comment import (
    SharedPublicationCommentRepository,
)
from app.schemas.shared_publication_comment import (
    GuestCommentEntry,
    GuestCommentList,
    SharedPublicationCommentList,
    SharedPublicationCommentRead,
)
from app.services.artifact_publication_access import ArtifactPublicationAccessService
from app.services.comment_mention import validate_mention_text
from app.services.publication_access_context import retry_publication_operation
from app.services.requirement_workflow_review import ANCHOR_FIELDS, matches_fixed_quote
from app.services.shared_comment_files import SharedCommentAccess, SharedCommentFiles
from app.services.shared_comment_mentions import SharedCommentMentions
from app.services.shared_publication import SharedPublicationService

QUOTE_FIELDS = {
    **ANCHOR_FIELDS,
    "specification": {"title", "summary"},
    "test_design": {"title"},
}


class SharedPublicationCommentService:
    def __init__(self):
        self.shared = SharedPublicationService()
        self.publications = ArtifactPublicationAccessService()
        self.repo = SharedPublicationCommentRepository()
        self.files = SharedCommentFiles(self.shared.context, self._access)
        self.mentions = SharedCommentMentions(self.shared.repository)

    def _access(self, db, original, actor, project_id, publication_id, write):
        if project_id is None:
            row, revision = self.shared._get(db, original, actor, publication_id)
            return SharedCommentAccess(
                can_post=row["comment_allowed"],
                author=row["participant_label"],
                expires_at=row["effective_expiry"],
                grant_id=row["grant_id"],
                revision=revision,
                download_allowed=row["download_allowed"] is not False,
            )
        row = self.publications._publication(db, actor, project_id, publication_id)
        if write and row.publication_state != "published":
            raise NotFoundError()
        may_publish = self.shared.context.authorization.has_project_permission(
            db,
            user=actor,
            project_id=project_id,
            permission_code="artifact_publication:publish",
        )
        return SharedCommentAccess(
            can_post=row.publication_state == "published" and may_publish,
            author=actor.name,
        )

    @retry_publication_operation
    def inbox(self, original, user, page, page_size, participation_id=None):
        with self.shared.context.transaction(original, user) as (db, actor):
            publications = self.shared.repository.shared_query(actor.id)
            cols = publications.selected_columns
            if participation_id is not None:
                publications = publications.where(
                    cols.participant_id == participation_id
                )
            publications = self.shared.context.read_projects(
                db, original, actor, publications, cols.project_id
            )
            allowed = publications.with_only_columns(
                cols.id, cols.title, cols.effective_expiry
            ).subquery()
            comment = SharedPublicationComment.__table__.c
            query = (
                select(
                    comment.id,
                    comment.body,
                    comment.parent_id,
                    comment.node_id,
                    comment.anchor,
                    comment.author_label,
                    comment.created_at,
                    comment.publication_id,
                    allowed.c.title.label("publication_title"),
                    allowed.c.effective_expiry.label("expires_at"),
                )
                .join(allowed, allowed.c.id == comment.publication_id)
                .where(comment.parent_id.is_(None))
            )
            rows, total = self.shared.repository.page(
                db, query, [comment.created_at.desc(), comment.id], page, page_size
            )
            ids = [row["id"] for row in rows]
            files = self.files.list_for_comments(db, ids)
            mentions = self.mentions.list_for_comments(db, ids)
            return GuestCommentList(
                items=[
                    GuestCommentEntry.model_validate(
                        {
                            **row,
                            "attachments": files[row["id"]],
                            "mentions": self.mentions.text(mentions[row["id"]]),
                        }
                    )
                    for row in rows
                ],
                total=total,
            )

    @retry_publication_operation
    def listing(
        self,
        original,
        user,
        publication_id,
        parent_id,
        page,
        page_size,
        project_id=None,
        *,
        thread_id=None,
    ):
        with self.shared.context.transaction(original, user, project_id) as (db, actor):
            allowed = self._access(
                db, original, actor, project_id, publication_id, False
            )
            if parent_id is not None and thread_id is not None:
                raise BadRequestError("返信先または共有スレッドを指定してください。")
            selected = parent_id or thread_id
            if (
                selected is not None
                and self.repo.thread(db, publication_id, selected) is None
            ):
                raise NotFoundError()
            rows, total = self.repo.list(
                db, publication_id, parent_id, page, page_size, thread_id
            )
            ids = [row.id for row in rows]
            files = self.files.list_for_comments(db, ids)
            mentions = self.mentions.list_for_comments(db, ids)
            return SharedPublicationCommentList(
                items=[
                    SharedPublicationCommentRead.model_validate(row).model_copy(
                        update={
                            "attachments": files[row.id],
                            "mentions": self.mentions.text(mentions[row.id]),
                        }
                    )
                    for row in rows
                ],
                total=total,
                can_post=allowed.can_post,
                download_allowed=allowed.download_allowed,
                expires_at=allowed.expires_at,
            )

    @retry_publication_operation
    def mention_candidates(
        self, original, user, publication_id, query, project_id=None
    ):
        with self.shared.context.transaction(original, user, project_id) as (db, actor):
            allowed = self._access(
                db, original, actor, project_id, publication_id, False
            )
            if not allowed.can_post:
                raise ForbiddenError()
            return self.mentions.candidates(db, publication_id, actor.id, query)

    @retry_publication_operation
    def create(self, original, user, publication_id, data, project_id=None):
        with self.shared.context.transaction(
            original, user, project_id, write=True
        ) as (db, actor):
            allowed = self._access(
                db, original, actor, project_id, publication_id, True
            )
            if not allowed.can_post:
                raise ForbiddenError()
            mentions = self.mentions.grouped(
                validate_mention_text(data.body, data.mentions)
            )
            identity = uuid5(
                NAMESPACE_URL,
                f"shared-comment:{actor.id}:{publication_id}:{data.request_id}",
            )
            parent = None
            if data.parent_id is not None:
                parent = self.repo.thread(db, publication_id, data.parent_id)
                if parent is None:
                    raise NotFoundError()
            if data.node_id is not None:
                node = self.repo.node(db, publication_id, data.node_id)
                if node is None:
                    raise NotFoundError()
                if data.anchor is not None and (
                    data.anchor.field not in QUOTE_FIELDS.get(node.node_kind, ())
                    or not data.anchor.quote.strip()
                    or not matches_fixed_quote(
                        node.payload.get(data.anchor.field), data.anchor
                    )
                ):
                    raise BadRequestError("引用する範囲が固定本文と一致しません。")
            anchor = data.anchor.model_dump() if data.anchor is not None else None
            row, inserted = self.repo.add(
                db,
                dict(
                    id=identity,
                    publication_id=publication_id,
                    parent_id=data.parent_id,
                    node_id=data.node_id,
                    anchor=anchor,
                    author_id=actor.id,
                    author_label=allowed.author,
                    body=data.body,
                ),
            )
            if row is None or (row.body, row.parent_id, row.node_id, row.anchor) != (
                data.body,
                data.parent_id,
                data.node_id,
                anchor,
            ):
                raise ConflictError()
            if inserted:
                files = self.files.bind(
                    db, row, actor.id, allowed.grant_id, data.attachment_ids
                )
                recipients = self.mentions.bind(db, row, actor.id, mentions)
                for recipient in recipients:
                    self._notify(
                        db, actor, row, parent, recipient, SHARED_COMMENT_MENTION
                    )
                if parent is not None and parent.author_id not in {
                    recipient["user_id"] for recipient in recipients
                }:
                    self._notify_reply(db, actor, parent, row)
            else:
                files = self.files.list_for_comments(db, [row.id])[row.id]
                saved_mentions = self.mentions.list_for_comments(db, [row.id])[row.id]
                if [
                    file.id for file in files
                ] != data.attachment_ids or saved_mentions != mentions:
                    raise ConflictError()
            return SharedPublicationCommentRead.model_validate(row).model_copy(
                update={"attachments": files, "mentions": self.mentions.text(mentions)}
            )

    def _notify_reply(self, db, actor, parent, reply):
        if parent.author_id == actor.id:
            return
        query = self.mentions.recipients(reply.publication_id)
        recipient = (
            db.execute(query.where(query.selected_columns.user_id == parent.author_id))
            .mappings()
            .first()
        )
        if recipient is not None:
            self._notify(db, actor, reply, parent, recipient, SHARED_COMMENT_REPLY)

    def _notify(self, db, actor, comment, parent, recipient, kind):
        if recipient["public_author_comment_id"] is not None:
            self.mentions.staff.emit(
                db,
                recipient,
                comment,
                NotificationType.MENTIONED
                if kind == SHARED_COMMENT_MENTION
                else NotificationType.SHARED_COMMENT_REPLIED,
            )
            return
        GuestActivityRepository().emit(
            db,
            tenant_id=recipient["tenant_id"],
            project_id=recipient["project_id"],
            actor_id=actor.id,
            recipient_id=recipient["user_id"],
            grant_id=recipient["grant_id"],
            publication_id=comment.publication_id,
            event_id=comment.id,
            kind=kind,
            thread_id=parent.id if parent is not None else comment.id,
        )
