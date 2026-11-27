"""固定公開版の共有コメント取得。"""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.models.artifact_publication import ArtifactPublicationNode
from app.models.artifact_publication_access import SharedPublicationComment


class SharedPublicationCommentRepository:
    def add(self, db, values):
        statement = insert(SharedPublicationComment).values(**values)
        inserted = db.scalar(
            statement.on_conflict_do_nothing(index_elements=["id"]).returning(
                SharedPublicationComment.id
            )
        )
        return db.get(SharedPublicationComment, values["id"]), inserted is not None

    def list(self, db, publication_id, parent_id, page, page_size, thread_id=None):
        query = select(SharedPublicationComment).where(
            SharedPublicationComment.publication_id == publication_id,
            SharedPublicationComment.parent_id == parent_id,
        )
        if thread_id is not None:
            query = query.where(SharedPublicationComment.id == thread_id)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        return db.scalars(
            query.order_by(
                SharedPublicationComment.created_at, SharedPublicationComment.id
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all(), total

    def thread(self, db, publication_id, identity):
        return db.scalar(
            select(SharedPublicationComment).where(
                SharedPublicationComment.id == identity,
                SharedPublicationComment.publication_id == publication_id,
                SharedPublicationComment.parent_id.is_(None),
            )
        )

    def node(self, db, publication_id, identity):
        return db.scalar(
            select(ArtifactPublicationNode).where(
                ArtifactPublicationNode.id == identity,
                ArtifactPublicationNode.publication_id == publication_id,
            )
        )
