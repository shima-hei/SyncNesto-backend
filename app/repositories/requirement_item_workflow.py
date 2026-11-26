"""一覧・詳細の個別要件に対応する現在値と依頼時の証跡を取得する。"""

from sqlalchemy import and_, func, select
from sqlalchemy.orm import aliased

from app.models.artifact_release import ArtifactRelease, ArtifactReleaseNode
from app.models.requirement import (
    Requirement,
    RequirementDetail,
    RequirementDocument,
    RequirementSection,
)
from app.models.requirement_workflow import (
    RequirementReviewConfirmation,
    RequirementReviewThread,
    RequirementWorkflowParticipant,
    RequirementWorkflowRequest,
)
from app.repositories.requirement_workflow import includes_requirement_node


class RequirementItemWorkflowRepository:
    """要件IDの集合から、承認版と対象のレビューを一括取得する。"""

    def current_items(self, db, project_id, ids):
        return (
            db.execute(
                select(Requirement, RequirementSection.id)
                .join(
                    RequirementDocument,
                    RequirementDocument.id == Requirement.document_id,
                )
                .outerjoin(
                    RequirementSection,
                    and_(
                        RequirementSection.id == Requirement.section_id,
                        RequirementSection.document_id == Requirement.document_id,
                        RequirementSection.deleted_at.is_(None),
                    ),
                )
                .where(
                    Requirement.id.in_(ids),
                    Requirement.deleted_at.is_(None),
                    RequirementDocument.project_id == project_id,
                    RequirementDocument.deleted_at.is_(None),
                )
                .order_by(Requirement.id)
            )
            .tuples()
            .all()
        )

    def latest_reviews(self, db, project_id, ids):
        request, node = RequirementWorkflowRequest, ArtifactReleaseNode
        parent = aliased(node)
        return (
            db.execute(
                select(request, node, ArtifactRelease.source_kinds, parent.source_id)
                .select_from(request)
                .join(node, includes_requirement_node())
                .join(ArtifactRelease, ArtifactRelease.id == request.release_id)
                .outerjoin(
                    parent,
                    and_(parent.id == node.parent_id, parent.node_kind == "section"),
                )
                .where(
                    request.project_id == project_id,
                    request.kind == "review",
                    request.status != "cancelled",
                    node.source_id.in_(ids),
                )
                .distinct(node.source_id)
                .order_by(node.source_id, request.display_number.desc())
            )
            .tuples()
            .all()
        )

    def baselines(self, db, project_id, document_ids):
        request = RequirementWorkflowRequest
        return (
            db.execute(
                select(request, ArtifactRelease.source_kinds)
                .select_from(request)
                .join(ArtifactRelease, ArtifactRelease.id == request.release_id)
                .where(
                    request.project_id == project_id,
                    request.document_id.in_(document_ids),
                    request.kind == "formal_approval",
                    request.status == "approved",
                )
                .distinct(request.document_id)
                .order_by(
                    request.document_id,
                    request.finalized_at.desc(),
                    request.created_at.desc(),
                    request.id.desc(),
                )
            )
            .tuples()
            .all()
        )

    def baseline_items(self, db, release_ids, ids):
        node = ArtifactReleaseNode
        parent = aliased(node)
        return (
            db.execute(
                select(node, parent.source_id)
                .outerjoin(
                    parent,
                    and_(parent.id == node.parent_id, parent.node_kind == "section"),
                )
                .where(
                    node.release_id.in_(release_ids),
                    node.node_kind == "requirement",
                    node.source_id.in_(ids),
                )
            )
            .tuples()
            .all()
        )

    @staticmethod
    def assigned(db, user_id, request_ids):
        participant = RequirementWorkflowParticipant
        return set(
            db.scalars(
                select(participant.request_id).where(
                    participant.user_id == user_id,
                    participant.request_id.in_(request_ids),
                )
            )
        )

    @staticmethod
    def current_details(db, ids):
        return db.scalars(
            select(RequirementDetail).where(RequirementDetail.requirement_id.in_(ids))
        ).all()

    @staticmethod
    def fixed_details(db, node_ids):
        node = ArtifactReleaseNode
        return db.scalars(
            select(node).where(
                node.parent_id.in_(node_ids), node.node_kind == "requirement_detail"
            )
        ).all()

    @staticmethod
    def review_evidence(db, requests, node_ids, ancestor_ids):
        """現在対象になっている要件の確認人数と未解決の指摘を集計する。"""
        participant = RequirementWorkflowParticipant
        confirmation = RequirementReviewConfirmation
        thread = RequirementReviewThread
        participant_counts = dict(
            db.execute(
                select(participant.request_id, func.count())
                .where(participant.request_id.in_(requests))
                .group_by(participant.request_id)
            )
            .tuples()
            .all()
        )
        counts = {
            (request_id, node_id): count
            for request_id, node_id, count in db.execute(
                select(confirmation.request_id, confirmation.node_id, func.count())
                .where(
                    confirmation.request_id.in_(requests),
                    confirmation.node_id.in_(node_ids),
                )
                .group_by(confirmation.request_id, confirmation.node_id)
            )
        }
        blocked = set(
            db.execute(
                select(thread.request_id, thread.node_id).where(
                    thread.request_id.in_(requests),
                    thread.node_id.in_(ancestor_ids),
                    thread.kind == "correction",
                    thread.status != "resolved",
                )
            )
            .tuples()
            .all()
        )
        return participant_counts, counts, blocked
