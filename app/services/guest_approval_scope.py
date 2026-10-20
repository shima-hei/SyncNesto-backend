"""外部合意の根拠と、公開された対象だけの固定scopeを作る。"""

from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import uuid5

from sqlalchemy import select

from app.core.exceptions import AppError, FixedApprovalScopeConflictError, NotFoundError
from app.db.base import Base
from app.models.fixed_approval import FixedApprovalRequest
from app.models.guest_fixed_approval import FixedApprovalGuestBinding
from app.schemas.artifact_release import ArtifactPreviewRequest
from app.schemas.fixed_approval import ApprovalBasisRead
from app.services.artifact_release_snapshot import ArtifactSnapshotBuilder, digest
from app.services.fixed_approval import FixedApprovalService
from app.services.fixed_approval_scope import requirement_digest

POLICY = "fixed-approval-guest-v1"
KINDS = {"requirement", "requirement_document", "specification", "test_design"}


def target_and_nodes(db, actor, project_id, publication, public_target):
    """内部の全sourceと公開nodeの固定対応を照合する。"""
    approvals = FixedApprovalService()
    nodes = approvals.repository.nodes(db, publication.release_id)
    mapping = publication.source_manifest["id_map"]
    internal_id = mapping.get(str(public_target))
    target = next((node for node in nodes if str(node.id) == internal_id), None)
    if target is None or target.node_kind not in KINDS:
        raise NotFoundError()
    release, _ = approvals.release(
        db, actor, project_id, publication.release_id, publication.preservation_id
    )
    if any(
        ref["kind"] not in {"specification", "test_design"}
        for ref in release.source_refs[1:]
    ):
        raise FixedApprovalScopeConflictError()
    if target.node_kind in {"specification", "test_design"} and not any(
        ref["kind"] == target.node_kind and ref["id"] == target.source_id
        for ref in release.source_refs
    ):
        raise FixedApprovalScopeConflictError()
    return release, nodes, target


def external_bases(db, project_id, nodes, target):
    """社内合意を外部合意として代用せず、同じ本文の根拠を固定する。"""
    if target.node_kind != "requirement_document":
        return []
    members = {target.id}
    for node in nodes:
        if node.parent_id in members:
            members.add(node.id)
    reqs = [
        node for node in nodes if node.id in members and node.node_kind == "requirement"
    ]
    previous = list(
        db.scalars(
            select(FixedApprovalRequest)
            .join(
                FixedApprovalGuestBinding,
                FixedApprovalGuestBinding.approval_id == FixedApprovalRequest.id,
            )
            .where(
                FixedApprovalRequest.project_id == project_id,
                FixedApprovalRequest.target_kind == "requirement",
                FixedApprovalRequest.sealed.is_(True),
            )
            .order_by(
                FixedApprovalRequest.created_at.desc(), FixedApprovalRequest.id.desc()
            )
        )
    )
    result = []
    for node in reqs:
        subject = requirement_digest(
            node,
            [
                child
                for child in nodes
                if child.parent_id == node.id
                and child.node_kind == "requirement_detail"
            ],
        )
        histories = [row for row in previous if row.source_id == node.source_id]
        same = [
            row
            for row in histories
            if row.scope_manifest.get("requirement_digest") == subject
        ]
        approved = [row for row in same if row.status == "approved"]
        rejected = any(row.status == "rejected" for row in same)
        state, agreement, event_id = "unagreed", None, None
        if approved and rejected:
            state = "conflicting"
        elif approved:
            agreement = approved[0].id
            event_id = FixedApprovalService().repository.events(db, agreement)[-1].id
            state = "agreed"
        elif rejected:
            state = "rejected"
        elif any(row.status == "requested" for row in same):
            state = "pending"
        elif histories:
            state = "changed"
        result.append(
            ApprovalBasisRead(
                node_id=node.id,
                source_id=node.source_id,
                source_version=node.source_version,
                title=node.payload["title"],
                subject_digest=subject,
                state=state,
                agreed_approval_id=agreement,
                agreed_event_id=event_id,
            )
        )
    return result


def public_bases(publication, bases):
    """他者の判断IDや氏名を含めず、公開済み要件への要約に変換する。"""
    reverse = {
        value: key for key, value in publication.source_manifest["id_map"].items()
    }
    return [
        dict(
            node_id=reverse[str(item.node_id)],
            title=item.title,
            state=item.state,
            reason=item.reason,
        )
        for item in bases
    ]


def manifest(release, target, nodes, preservation, bases) -> dict[str, Any]:
    """4種の対象を既存の固定承認正本へ保存できる形にする。"""
    children = [n for n in nodes if n.parent_id == target.id]
    return dict(
        policy=POLICY,
        release_id=str(release.id),
        body_digest=release.content_digest,
        target_node_id=str(target.id),
        target_kind=target.node_kind,
        source_id=target.source_id,
        source_version=target.source_version,
        title=target.payload["title"],
        requirement_digest=requirement_digest(
            target, [n for n in children if n.node_kind == "requirement_detail"]
        )
        if target.node_kind == "requirement"
        else None,
        subject_digest=digest(
            [
                dict(
                    kind=n.node_kind,
                    source_id=n.source_id,
                    version=n.source_version,
                    payload=n.payload,
                )
                for n in [target, *children]
            ]
        ),
        supporting_sources=release.source_refs[1:],
        preservation_id=str(preservation.id) if preservation else None,
        preservation_digest=preservation.manifest_digest if preservation else None,
        files=[
            {**item, "file_id": str(uuid5(preservation.id, str(index)))}
            for index, item in enumerate(preservation.manifest["files"])
        ]
        if preservation
        else [],
        basis_count=len(bases),
        basis_digest=digest([b.model_dump(mode="json") for b in bases]),
        exception_count=sum(b.state != "agreed" for b in bases),
        legacy_origin=None,
    )


def compare_current(db, project_id, release):
    """保存された同Projectのsourceだけを読み、本文自体は外部へ返さない。"""
    refs = []
    for ref in release.source_refs:
        table = Base.metadata.tables[
            dict(
                requirement="requirements",
                requirement_document="requirement_documents",
                specification="specifications",
                test_design="test_designs",
            )[ref["kind"]]
        ]
        query = select(table.c.version).where(
            table.c.id == ref["id"], table.c.deleted_at.is_(None)
        )
        if ref["kind"] == "requirement":
            doc = Base.metadata.tables["requirement_documents"]
            query = query.join(doc, doc.c.id == table.c.document_id).where(
                doc.c.project_id == project_id, doc.c.deleted_at.is_(None)
            )
        else:
            query = query.where(table.c.project_id == project_id)
        version = db.scalar(query)
        if version is None:
            return "unavailable"
        refs.append({**ref, "version": version})
    try:
        snapshot = ArtifactSnapshotBuilder().build(
            db,
            project_id,
            ArtifactPreviewRequest(source=refs[0], included_sources=refs[1:]),
            datetime.now(UTC),
        )
        return "equal" if snapshot.scope_digest == release.content_digest else "changed"
    except AppError:
        return "unavailable"


def counts(bases):
    """申請時の全要件の状態を数える。"""
    return dict(Counter(item.state for item in bases))
