"""固定版のレビュー確認・コメント・修正依頼を取得し更新する。"""

from uuid import UUID

from sqlalchemy import delete, exists, func, insert, literal, select
from sqlalchemy.orm import Session

from app.models.artifact_release import ArtifactReleaseNode
from app.models.requirement_workflow import (
    RequirementReviewConfirmation,
    RequirementReviewThread,
    RequirementReviewThreadEvent,
    RequirementWorkflowParticipant,
    RequirementWorkflowRequest,
)
from app.repositories.requirement_workflow import (
    RequirementWorkflowRepository,
    release_descendants,
)


class RequirementReviewRepository:
    """レビュー範囲の要件と、同じ依頼に属する操作だけを扱う。"""

    @staticmethod
    def participant(db: Session, request_id: UUID, user_id: int):
        """指定された本人の参加を取得する。"""
        return db.get(RequirementWorkflowParticipant, (request_id, user_id))

    @staticmethod
    def node(db: Session, row: RequirementWorkflowRequest, node_id: UUID):
        """章・要件・文書のうち、レビュー範囲に入る固定内容だけを返す。"""
        return db.scalar(
            RequirementWorkflowRepository.scoped_nodes(row).where(
                ArtifactReleaseNode.id == node_id,
                ArtifactReleaseNode.node_kind.in_(
                    ("requirement_document", "section", "requirement")
                ),
            )
        )

    @staticmethod
    def _requirements(row: RequirementWorkflowRequest):
        """確認対象は選択範囲内の個別要件。"""
        return (
            RequirementWorkflowRepository.scoped_nodes(row)
            .where(ArtifactReleaseNode.node_kind == "requirement")
            .with_only_columns(ArtifactReleaseNode.id)
        )

    @staticmethod
    def _blocked(row: RequirementWorkflowRequest):
        """未解決の修正指摘の対象と、その配下にある固定要件。"""
        thread = RequirementReviewThread
        roots = select(ArtifactReleaseNode.__table__.c.id).where(
            ArtifactReleaseNode.__table__.c.release_id == row.release_id,
            ArtifactReleaseNode.__table__.c.id.in_(
                select(thread.node_id).where(
                    thread.request_id == row.id,
                    thread.kind == "correction",
                    thread.status != "resolved",
                )
            ),
        )
        descendants = release_descendants(
            row.release_id, roots, "requirement_review_blocked"
        )
        return select(descendants.c.id)

    @staticmethod
    def _confirmed(request_id: UUID, user_id: int, node_id):
        """押下・一括操作によって本人が記録した確認だけを対象にする。"""
        confirmation = RequirementReviewConfirmation
        return exists().where(
            confirmation.request_id == request_id,
            confirmation.user_id == user_id,
            confirmation.node_id == node_id,
        )

    def counts(self, db: Session, row: RequirementWorkflowRequest, user_id: int):
        """固定要件の件数と、現在有効な本人確認・修正待ちを集計する。"""
        requirements = self._requirements(row).subquery()
        blocked = requirements.c.id.in_(self._blocked(row))
        confirmed = self._confirmed(row.id, user_id, requirements.c.id)
        return (
            db.execute(
                select(
                    func.count(requirements.c.id),
                    func.count(requirements.c.id).filter(~blocked, confirmed),
                    func.count(requirements.c.id).filter(blocked),
                )
            )
            .one()
            ._tuple()
        )

    def flags(
        self,
        db: Session,
        row: RequirementWorkflowRequest,
        user_id: int,
        ids: list[UUID],
    ):
        """表示中の要件だけの確認・修正待ちを判定する。"""
        if not ids:
            return []
        requirements = self._requirements(row).subquery()
        return (
            db.execute(
                select(
                    requirements.c.id,
                    self._confirmed(row.id, user_id, requirements.c.id),
                    requirements.c.id.in_(self._blocked(row)),
                ).where(requirements.c.id.in_(ids))
            )
            .tuples()
            .all()
        )

    def set_confirmation(
        self,
        db: Session,
        row: RequirementWorkflowRequest,
        user_id: int,
        node_id: UUID,
        confirmed: bool,
    ) -> None:
        """本人の確認を付けるか取り消す。修正待ちの判定は呼び出し元で行う。"""
        existing = db.get(RequirementReviewConfirmation, (row.id, user_id, node_id))
        if confirmed and existing is None:
            db.add(
                RequirementReviewConfirmation(
                    request_id=row.id, user_id=user_id, node_id=node_id
                )
            )
        if not confirmed and existing is not None:
            db.delete(existing)
        db.flush()

    def confirm_remaining(
        self, db: Session, row: RequirementWorkflowRequest, user_id: int
    ):
        """修正待ちと確認済みを除き、未確認のものだけを一括保存する。"""
        requirements = self._requirements(row).subquery()
        db.execute(
            insert(RequirementReviewConfirmation).from_select(
                ["request_id", "user_id", "node_id"],
                select(literal(row.id), literal(user_id), requirements.c.id).where(
                    ~requirements.c.id.in_(self._blocked(row)),
                    ~self._confirmed(row.id, user_id, requirements.c.id),
                ),
            )
        )

    def clear_blocked_pending_confirmations(
        self, db: Session, row: RequirementWorkflowRequest
    ):
        """提出済みの記録を維持し、未提出者の影響範囲だけ再確認へ戻す。"""
        participant = RequirementWorkflowParticipant
        db.execute(
            delete(RequirementReviewConfirmation)
            .where(
                RequirementReviewConfirmation.request_id == row.id,
                RequirementReviewConfirmation.node_id.in_(self._blocked(row)),
                RequirementReviewConfirmation.user_id.in_(
                    select(participant.user_id).where(
                        participant.request_id == row.id,
                        participant.status == "pending",
                    )
                ),
            )
            .execution_options(synchronize_session=False)
        )

    @staticmethod
    def participant_statuses(db: Session, request_id: UUID) -> list[str]:
        """提出状況を集約するため、依頼の全担当者の状態を取得する。"""
        participant = RequirementWorkflowParticipant
        return list(
            db.scalars(
                select(participant.status).where(participant.request_id == request_id)
            )
        )

    @staticmethod
    def correction_count(db: Session, request_id: UUID) -> int:
        """対象範囲内の未解決の修正依頼を集計する。"""
        item = RequirementReviewThread
        return (
            db.scalar(
                select(func.count())
                .select_from(item)
                .where(
                    item.request_id == request_id,
                    item.kind == "correction",
                    item.status != "resolved",
                )
            )
            or 0
        )

    @staticmethod
    def thread(db: Session, request_id: UUID, thread_id: UUID):
        """同じ依頼内のコメントまたは修正依頼を取得する。"""
        item = RequirementReviewThread
        return db.scalar(
            select(item).where(item.request_id == request_id, item.id == thread_id)
        )

    @staticmethod
    def threads(
        db: Session,
        request_id: UUID,
        page: int,
        page_size: int,
        *,
        active_only: bool,
        node_id: UUID | None,
        author_id: int | None = None,
    ):
        """依頼に属する指摘をページ取得する。"""
        item = RequirementReviewThread
        query = select(item).where(item.request_id == request_id)
        if node_id is not None:
            query = query.where(item.node_id == node_id)
        if active_only:
            query = query.where(item.status != "resolved")
        if author_id is not None:
            query = query.where(item.author_id == author_id)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = list(
            db.scalars(
                query.order_by(item.created_at.desc(), item.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    @staticmethod
    def events(db: Session, thread_id: UUID, page: int, page_size: int):
        """返信・状態変更を発生順にページ取得する。"""
        item = RequirementReviewThreadEvent
        query = select(item).where(item.thread_id == thread_id)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = list(
            db.scalars(
                query.order_by(item.id).offset((page - 1) * page_size).limit(page_size)
            )
        )
        return rows, total
