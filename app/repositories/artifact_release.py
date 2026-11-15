"""固定版の追加と、認可条件を受け取る読み取り。途中でcommitしない。"""

from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.models.artifact_release import ArtifactRelease, ArtifactReleaseNode


class ArtifactReleaseRepository:
    """元データを書き換えず、固定版ヘッダーと本文を保存する。"""

    def by_request(self, db: Session, project_id: int, actor_id: int, request_id: UUID):
        """本人・Project内の再送だけを照合する。"""
        return db.scalar(
            select(ArtifactRelease).where(
                ArtifactRelease.project_id == project_id,
                ArtifactRelease.created_by == actor_id,
                ArtifactRelease.request_id == request_id,
                ArtifactRelease.sealed.is_(True),
            )
        )

    def get(self, db: Session, project_id: int, release_id: UUID):
        """完成した同一Projectの固定版だけを取得する。"""
        return db.scalar(
            select(ArtifactRelease).where(
                ArtifactRelease.id == release_id,
                ArtifactRelease.project_id == project_id,
                ArtifactRelease.sealed.is_(True),
            )
        )

    def visible(self, project_id: int, kinds: list[str]):
        """件数と一覧に共通する、全種類の許可条件を返す。"""
        return select(ArtifactRelease).where(
            ArtifactRelease.project_id == project_id,
            ArtifactRelease.sealed.is_(True),
            ArtifactRelease.source_kinds.contained_by(kinds),
        )

    def create(
        self,
        db: Session,
        header: dict,
        nodes: list[dict],
    ) -> ArtifactRelease:
        """ヘッダー、同じ固定版内の本文、封印を同じtransactionに保存する。"""
        row = ArtifactRelease(**header)
        db.add(row)
        db.flush()
        db.execute(insert(ArtifactReleaseNode), nodes)
        row.sealed = True
        db.flush()
        db.refresh(row)
        return row

    def nodes(self, db: Session, release_id: UUID, after: int, limit: int):
        """安定した通番で、次ページ判定用の1件を加えて読む。"""
        return list(
            db.scalars(
                select(ArtifactReleaseNode)
                .where(
                    ArtifactReleaseNode.release_id == release_id,
                    ArtifactReleaseNode.ordinal > after,
                )
                .order_by(ArtifactReleaseNode.ordinal)
                .limit(limit + 1)
            )
        )
