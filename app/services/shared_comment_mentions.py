"""固定版を現在閲覧できる参加者へのメンション。"""

from collections import defaultdict

from sqlalchemy import String, Uuid, case, cast, func, literal, select, union_all

from app.core.exceptions import BadRequestError
from app.models.artifact_publication_access import SharedCommentMention
from app.models.project import Project
from app.models.review_account import ReviewAccountGrant
from app.models.user import User
from app.repositories.shared_comment_staff import SharedCommentStaffRepository
from app.schemas.comment_mention import CommentMentionText
from app.schemas.shared_publication_comment import SharedCommentMentionCandidate


class SharedCommentMentions:
    def __init__(self, access):
        self.access = access
        self.staff = SharedCommentStaffRepository()

    @staticmethod
    def recipient_key(reference, *, staff=False):
        """候補の識別子と許可管理に使うIDを分ける。"""
        namespace = "shared-staff-mention:" if staff else "shared-comment-mention:"
        value = literal(namespace) + cast(reference, String)
        return cast(
            func.substr(
                func.encode(func.sha256(func.convert_to(value, "UTF8")), "hex"), 1, 32
            ),
            Uuid,
        )

    def recipients(self, publication_id):
        publication = self.access.publications
        query = self.access.shared_query()
        columns = query.selected_columns
        project = Project.__table__.c
        user = User.__table__.c
        review = ReviewAccountGrant.__table__.c
        guests = (
            query.join(Project.__table__, project.id == publication.c.project_id)
            .join(User.__table__, user.id == columns.participant_user_id)
            .where(
                publication.c.id == publication_id,
                user.password_change_required.is_(False),
                ~select(review.user_id).where(review.user_id == user.id).exists(),
            )
            .with_only_columns(
                self.recipient_key(columns.grant_id).label("id"),
                columns.grant_id,
                columns.participant_label.label("name"),
                columns.participant_user_id.label("user_id"),
                project.tenant_id,
                project.id.label("project_id"),
                cast(None, Uuid).label("public_author_comment_id"),
            )
        )
        authors = self.staff.authors(publication_id).subquery()
        staff = select(
            self.recipient_key(authors.c.id, staff=True).label("id"),
            cast(None, Uuid).label("grant_id"),
            authors.c.name,
            authors.c.user_id,
            authors.c.tenant_id,
            authors.c.project_id,
            authors.c.id.label("public_author_comment_id"),
        )
        return select(union_all(guests, staff).subquery())

    def candidates(self, db, publication_id, actor_id, search):
        query = self.recipients(publication_id)
        columns = query.selected_columns
        rows = db.execute(
            query.where(
                columns.user_id != actor_id,
                columns.name.icontains(search, autoescape=True),
            )
            .with_only_columns(columns.id, columns.name)
            .order_by(columns.name, columns.id)
            .limit(30)
        ).mappings()
        return [SharedCommentMentionCandidate.model_validate(row) for row in rows]

    @staticmethod
    def grouped(mentions):
        groups = defaultdict(list)
        for mention in mentions:
            groups[mention.recipient_id].append(
                CommentMentionText.model_validate(mention.model_dump()).model_dump()
            )
        return groups

    def list_for_comments(self, db, comment_ids):
        groups = {comment_id: {} for comment_id in comment_ids}
        if groups:
            item = SharedCommentMention
            for comment_id, recipient_id, occurrences in db.execute(
                select(
                    item.comment_id,
                    case(
                        (item.grant_id.is_not(None), self.recipient_key(item.grant_id)),
                        else_=self.recipient_key(
                            item.public_author_comment_id, staff=True
                        ),
                    ),
                    item.occurrences,
                ).where(item.comment_id.in_(groups))
            ):
                groups[comment_id][recipient_id] = occurrences
        return groups

    @staticmethod
    def text(groups):
        items = [
            CommentMentionText.model_validate(item)
            for group in groups.values()
            for item in group
        ]
        return sorted(items, key=lambda item: item.start)

    def bind(self, db, comment, actor_id, groups):
        if not groups:
            return []
        query = self.recipients(comment.publication_id)
        columns = query.selected_columns
        recipients = {
            row["id"]: row
            for row in db.execute(
                query.where(columns.id.in_(groups), columns.user_id != actor_id)
            ).mappings()
        }
        for recipient_id, group in groups.items():
            recipient = recipients.get(recipient_id)
            if recipient is None or any(
                item["display_name"] != recipient["name"] for item in group
            ):
                raise BadRequestError("メンション先を選び直してください。")
            db.add(
                SharedCommentMention(
                    publication_id=comment.publication_id,
                    comment_id=comment.id,
                    recipient_ref_id=recipient["grant_id"]
                    or recipient["public_author_comment_id"],
                    grant_id=recipient["grant_id"],
                    public_author_comment_id=recipient["public_author_comment_id"],
                    occurrences=group,
                )
            )
        return list(recipients.values())
