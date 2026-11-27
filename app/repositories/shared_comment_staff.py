"""共有投稿に既に登場した社内本人と、その固定版の通知を照合する。"""

from sqlalchemy import String, and_, exists, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.db.base import Base
from app.models.artifact_publication_access import (
    SharedCommentMention,
    SharedPublicationComment,
)
from app.models.notification import (
    Notification,
    NotificationTargetType,
    NotificationType,
)
from app.repositories.rbac import (
    active_internal_project_identity,
    project_role_permission,
)

TARGET = NotificationTargetType.SHARED_PUBLICATION_COMMENT.value


class SharedCommentStaffRepository:
    def __init__(self):
        self.publication = Base.metadata.tables["artifact_publications"]
        self.project = Base.metadata.tables["projects"]
        self.comment = SharedPublicationComment.__table__
        self.notification = Notification.__table__

    def access(self, user_id):
        """社内の固定版取得と同じ所属と全構成の読み取り権限を要求する。"""
        pub, project = self.publication, self.project
        kinds = pub.c.source_kinds

        def allowed(code):
            return project_role_permission(project.c.id, user_id, code)

        return and_(
            pub.c.sealed.is_(True),
            pub.c.publication_state == "published",
            active_internal_project_identity(project, user_id),
            allowed("artifact_publication:read"),
            allowed("artifact_release:read"),
            kinds.contained_by(
                ["requirement", "requirement_document", "specification", "test_design"]
            ),
            or_(
                and_(
                    ~kinds.contains(["requirement"]),
                    ~kinds.contains(["requirement_document"]),
                ),
                allowed("requirement:read"),
            ),
            or_(
                and_(
                    ~kinds.contains(["specification"]), pub.c.preservation_id.is_(None)
                ),
                allowed("document:read"),
            ),
            or_(~kinds.contains(["test_design"]), allowed("test_plan:read")),
        )

    def _publication_comment(self, query):
        pub, project, comment = self.publication, self.project, self.comment
        return query.join(pub, pub.c.id == comment.c.publication_id).join(
            project, project.c.id == pub.c.project_id
        )

    def authors(self, publication_id):
        """各本人がこの版で先に公開した表示名を候補とする。"""
        pub, project, comment = self.publication, self.project, self.comment
        return (
            self._publication_comment(
                select(
                    comment.c.id,
                    comment.c.author_label.label("name"),
                    comment.c.author_id.label("user_id"),
                    project.c.tenant_id,
                    project.c.id.label("project_id"),
                ).select_from(comment)
            )
            .where(pub.c.id == publication_id, self.access(comment.c.author_id))
            .distinct(comment.c.author_id)
            .order_by(comment.c.author_id, comment.c.created_at, comment.c.id)
        )

    def visible(self, user_id, tenant_id):
        """通知の生成根拠、宛先と現在権限を一覧・件数・既読に適用する。"""
        n, pub, project, comment = (
            self.notification,
            self.publication,
            self.project,
            self.comment,
        )
        mention = SharedCommentMention.__table__
        author = comment.alias("shared_notice_recipient")
        parent = comment.alias("shared_notice_parent")
        explicitly_mentioned = exists(
            select(mention.c.comment_id)
            .join(
                author,
                and_(
                    author.c.id == mention.c.public_author_comment_id,
                    author.c.publication_id == mention.c.publication_id,
                ),
            )
            .where(
                mention.c.comment_id == comment.c.id,
                mention.c.publication_id == pub.c.id,
                author.c.author_id == user_id,
            )
            .correlate_except(mention, author)
        )
        parent_author = exists(
            select(parent.c.id)
            .where(
                parent.c.id == comment.c.parent_id,
                parent.c.publication_id == pub.c.id,
                parent.c.parent_id.is_(None),
                parent.c.author_id == user_id,
            )
            .correlate_except(parent)
        )
        return (
            self._publication_comment(
                select(
                    n.c.id,
                    pub.c.id.label("publication_id"),
                    pub.c.title,
                    project.c.name.label("project_name"),
                    comment.c.author_label,
                    func.coalesce(comment.c.parent_id, comment.c.id).label("thread_id"),
                ).select_from(comment)
            )
            .join(
                n,
                and_(
                    n.c.target_id == comment.c.id.cast(String),
                    n.c.project_id == project.c.id,
                    n.c.tenant_id == project.c.tenant_id,
                    n.c.actor_user_id == comment.c.author_id,
                ),
            )
            .where(
                n.c.tenant_id == tenant_id,
                n.c.recipient_user_id == user_id,
                n.c.actor_user_id != user_id,
                n.c.target_type == TARGET,
                n.c.event_key
                == literal(TARGET + ":") + n.c.type + ":" + comment.c.id.cast(String),
                or_(
                    and_(
                        n.c.type == NotificationType.MENTIONED.value,
                        explicitly_mentioned,
                    ),
                    and_(
                        n.c.type == NotificationType.SHARED_COMMENT_REPLIED.value,
                        parent_author,
                    ),
                ),
                self.access(user_id),
            )
        )

    def emit(self, db, recipient, comment, kind):
        """共有コメント本文や個人情報を写さず、通常の通知へ登録する。"""
        db.execute(
            insert(Notification)
            .values(
                tenant_id=recipient["tenant_id"],
                project_id=recipient["project_id"],
                recipient_user_id=recipient["user_id"],
                actor_user_id=comment.author_id,
                target_type=TARGET,
                target_id=str(comment.id),
                type=kind.value,
                event_key=f"{TARGET}:{kind.value}:{comment.id}",
                snapshot={},
                context={},
            )
            .on_conflict_do_nothing(index_elements=["event_key", "recipient_user_id"])
        )
