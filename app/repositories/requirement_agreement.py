"""表示対象の現在内容と、判断の小さな代表集合だけをDBから取得する。"""

from sqlalchemy import (
    Integer,
    String,
    Text,
    and_,
    case,
    cast,
    column,
    func,
    literal,
    or_,
    select,
    values,
)
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by

from app.models.artifact_release import ArtifactRelease, ArtifactReleaseNode
from app.models.fixed_approval import FixedApprovalEvent, FixedApprovalRequest
from app.models.guest_fixed_approval import FixedApprovalGuestBinding
from app.models.requirement import Requirement, RequirementDetail, RequirementDocument
from app.repositories.fixed_approval import FixedApprovalRepository
from app.repositories.requirement_trace import RequirementTraceRepository

MAX_SUBJECT_BYTES = 1024 * 1024
MAX_BATCH_BYTES = 8 * 1024 * 1024
MAX_DETAILS = 500
PAYLOAD_FIELDS = (
    "requirement_code",
    "requirement_type",
    "category",
    "title",
    "description",
    "rationale",
    "acceptance_criteria",
    "priority",
    "status",
    "source",
)


class RequirementAgreementRepository:
    """1件ごとのAPI/SQLや、固定版本文/全イベントの取得を避ける。"""

    def subjects(self, db, project_id, ids):
        """容量判定と条件付き投影を1 statementで行い、batchも制限する。"""
        payload = func.jsonb_build_object(
            *[
                part
                for name in PAYLOAD_FIELDS
                for part in (name, getattr(Requirement, name))
            ]
        )
        detail = func.jsonb_build_object(
            "source_id",
            RequirementDetail.id,
            "source_version",
            literal(None),
            "payload",
            func.jsonb_build_object(
                "detail_type",
                RequirementDetail.detail_type,
                "detail_json",
                RequirementDetail.detail_json,
            ),
        )
        details = (
            select(
                RequirementDetail.requirement_id.label("rid"),
                func.count().label("count"),
                func.sum(func.octet_length(cast(detail, Text)) + 2).label("bytes"),
            )
            .where(RequirementDetail.requirement_id.in_(ids))
            .group_by(RequirementDetail.requirement_id)
            .cte("detail_budget")
        )
        size = (
            func.octet_length(cast(payload, Text))
            + func.coalesce(details.c.bytes, 0)
            + 256
        )
        within = and_(
            size <= MAX_SUBJECT_BYTES, func.coalesce(details.c.count, 0) <= MAX_DETAILS
        )
        budget = (
            select(
                Requirement.id.label("id"),
                Requirement.version.label("version"),
                size.label("bytes"),
                within.label("within"),
                func.sum(case((within, size), else_=0))
                .over(order_by=Requirement.id)
                .label("running"),
            )
            .join(
                RequirementDocument, RequirementDocument.id == Requirement.document_id
            )
            .outerjoin(
                details,
                details.c.rid == Requirement.id,
            )
            .where(
                *RequirementTraceRepository().scope(project_id), Requirement.id.in_(ids)
            )
            .cte("subject_budget")
        )
        detail_rows = (
            select(func.jsonb_agg(aggregate_order_by(detail, RequirementDetail.id)))
            .where(RequirementDetail.requirement_id == Requirement.id)
            .correlate(Requirement)
            .scalar_subquery()
        )
        subject = func.jsonb_build_object(
            "policy",
            "req-subject-v1",
            "source_id",
            Requirement.id,
            "source_version",
            Requirement.version,
            "payload",
            payload,
            "details",
            func.coalesce(detail_rows, cast(literal("[]"), JSONB)),
        )
        return list(
            db.execute(
                select(
                    budget.c.id,
                    budget.c.version,
                    case(
                        (
                            and_(budget.c.within, budget.c.running <= MAX_BATCH_BYTES),
                            subject,
                        ),
                        else_=None,
                    ).label("subject"),
                )
                .join(Requirement, Requirement.id == budget.c.id)
                .order_by(budget.c.id)
            ).mappings()
        )

    def evidence(self, db, project_id, subjects):
        """各状態の代表だけを返し、古い大量の依頼をPythonへ読み込まない。"""
        current = (
            values(column("rid", Integer), column("digest", String))
            .data(list(subjects.items()))
            .cte("current_subjects")
        )
        req, event = FixedApprovalRequest, FixedApprovalEvent
        external = FixedApprovalGuestBinding.approval_id.is_not(None)
        same = req.scope_manifest["requirement_digest"].as_string() == current.c.digest
        valid = func.coalesce(
            and_(
                ArtifactReleaseNode.id.is_not(None),
                ArtifactReleaseNode.node_kind == "requirement",
                ArtifactReleaseNode.source_id == req.source_id,
                req.scope_manifest["requirement_digest"]
                .as_string()
                .op("~")("^[0-9a-f]{64}$"),
                event.id.is_not(None),
                event.scope_digest == req.scope_digest,
                event.action == req.status,
                or_(
                    req.status == "cancelled",
                    and_(req.status == "requested", event.actor_id == req.requester_id),
                    and_(
                        req.status.in_(["approved", "rejected"]),
                        event.actor_id == req.approver_id,
                    ),
                ),
            ),
            False,
        )
        candidates = (
            select(
                req.source_id.label("rid"),
                external.label("external"),
                same.label("same"),
                req.status,
                valid.label("valid"),
                req.id.label("approval_id"),
                event.id.label("event_id"),
                func.row_number()
                .over(
                    partition_by=(req.source_id, external, same, req.status, valid),
                    order_by=(req.created_at.desc(), req.id.desc()),
                )
                .label("rank"),
            )
            .join(current, current.c.rid == req.source_id)
            .join(
                ArtifactRelease,
                and_(
                    ArtifactRelease.id == req.release_id,
                    ArtifactRelease.project_id == project_id,
                    ArtifactRelease.sealed.is_(True),
                ),
            )
            .outerjoin(
                ArtifactReleaseNode,
                and_(
                    ArtifactReleaseNode.id == req.target_node_id,
                    ArtifactReleaseNode.release_id == req.release_id,
                ),
            )
            .outerjoin(
                FixedApprovalGuestBinding,
                FixedApprovalGuestBinding.approval_id == req.id,
            )
            .outerjoin(
                event,
                and_(
                    event.approval_id == req.id,
                    event.seq == case((req.status == "requested", 1), else_=2),
                ),
            )
            .where(
                req.project_id == project_id,
                req.target_kind == "requirement",
                req.sealed.is_(True),
                ArtifactRelease.source_kinds.contained_by(
                    [
                        "requirement",
                        "requirement_document",
                        "specification",
                        "test_design",
                    ]
                ),
            )
            .subquery()
        )
        return list(
            db.execute(select(candidates).where(candidates.c.rank == 1)).mappings()
        )

    def legacy_ids(self, db, project_id, ids):
        """従来の本文や理由を読まず、該当する要件IDだけを取得する。"""
        legacy = FixedApprovalRepository().legacy_visible(project_id).subquery()
        return set(
            db.scalars(
                select(legacy.c.target_id)
                .where(
                    legacy.c.target_type == "requirement_item",
                    legacy.c.target_id.in_(ids),
                )
                .distinct()
            )
        )
