"""添付構成と保全試行の取得。commitと認可はServiceで行う。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.artifact_preservation import (
    ArtifactPreservation,
    ArtifactPreservationObject,
)


class ArtifactPreservationRepository:
    """同じ本文固定版に属する構成と試行だけを取得する。"""

    def get(self, db: Session, release_id: UUID, preservation_id: UUID, *, lock=False):
        """状態遷移時は親行をロックする。"""
        query = select(ArtifactPreservation).where(
            ArtifactPreservation.id == preservation_id,
            ArtifactPreservation.release_id == release_id,
        )
        return db.scalar(query.with_for_update() if lock else query)

    def by_request(self, db, release_id, actor_id, request_id):
        """本文・本人・操作IDの再送を探す。"""
        return db.scalar(
            select(ArtifactPreservation).where(
                ArtifactPreservation.release_id == release_id,
                ArtifactPreservation.created_by == actor_id,
                ArtifactPreservation.request_id == request_id,
            )
        )

    def objects(self, db, preservation_id):
        """採用されなかった試行も回収用に保持する。"""
        return list(
            db.scalars(
                select(ArtifactPreservationObject)
                .where(ArtifactPreservationObject.preservation_id == preservation_id)
                .order_by(
                    ArtifactPreservationObject.created_at, ArtifactPreservationObject.id
                )
            )
        )
