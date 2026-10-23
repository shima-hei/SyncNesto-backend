"""公開準備版を途中commitせず保存し、明示したProjectで読む。"""

from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.models.artifact_publication import ArtifactPublication, ArtifactPublicationNode


class ArtifactPublicationRepository:
    """公開許可を作らず、封印した準備版だけを扱う。"""

    def get(self, db: Session, project_id: int, identity: UUID):
        """同じProjectの封印済み版を取得する。"""
        return db.scalar(
            select(ArtifactPublication).where(
                ArtifactPublication.project_id == project_id,
                ArtifactPublication.id == identity,
                ArtifactPublication.sealed.is_(True),
            )
        )

    def by_request(self, db: Session, project_id: int, actor_id: int, request_id: UUID):
        """本人の同一操作の再送だけを取得する。"""
        return db.scalar(
            select(ArtifactPublication).where(
                ArtifactPublication.project_id == project_id,
                ArtifactPublication.created_by == actor_id,
                ArtifactPublication.request_id == request_id,
                ArtifactPublication.sealed.is_(True),
            )
        )

    def visible(self, project_id, kinds, document_read):
        """件数と一覧へ全構成のread条件を適用する。"""
        query = select(ArtifactPublication).where(
            ArtifactPublication.project_id == project_id,
            ArtifactPublication.sealed.is_(True),
            ArtifactPublication.source_kinds.contained_by(kinds),
        )
        if not document_read:
            query = query.where(ArtifactPublication.preservation_id.is_(None))
        return query

    def create(self, db: Session, header: dict, nodes: list[dict]):
        """ヘッダーと本文を追加し、完結した版を封印する。"""
        row = ArtifactPublication(**header)
        db.add(row)
        db.flush()
        values = [
            dict(
                **{k: v for k, v in n.items() if k not in {"id", "parent_id"}},
                id=UUID(n["id"]),
                parent_id=UUID(n["parent_id"]) if n["parent_id"] else None,
                publication_id=row.id,
            )
            for n in nodes
        ]
        db.execute(insert(ArtifactPublicationNode), values)
        row.sealed = True
        db.flush()
        db.refresh(row)
        return row

    def nodes(self, db: Session, identity: UUID, after: int, limit: int):
        """通番でbyte上限調整用の候補を得る。"""
        return list(
            db.scalars(
                select(ArtifactPublicationNode)
                .where(
                    ArtifactPublicationNode.publication_id == identity,
                    ArtifactPublicationNode.ordinal > after,
                )
                .order_by(ArtifactPublicationNode.ordinal)
                .limit(limit)
            )
        )
