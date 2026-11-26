"""要件定義書全体の正式承認と、不変の開発基準を管理する。"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    DuplicateResourceError,
    ForbiddenError,
    NotFoundError,
    VersionConflictError,
)
from app.models.artifact_release import ArtifactReleaseNode
from app.models.project import Project
from app.models.requirement_workflow import (
    RequirementWorkflowParticipant,
    RequirementWorkflowRequest,
)
from app.models.user import User
from app.schemas.artifact_release import ArtifactCreateRequest
from app.schemas.requirement_workflow import (
    RequirementFormalApprovalBasis,
    RequirementFormalApprovalBlocker,
    RequirementFormalApprovalCreate,
    RequirementFormalApprovalDecision,
    RequirementFormalApprovalDecisionInput,
    RequirementFormalApprovalDecisionPreview,
    RequirementFormalApprovalInput,
    RequirementFormalApprovalPreview,
    RequirementReviewInput,
    RequirementWorkflowRead,
    RequirementWorkflowRecipient,
)
from app.services.artifact_release_snapshot import digest
from app.services.audit_log import AuditLogService
from app.services.requirement_baseline_changes import (
    CONTENT_KINDS,
    compare_content,
    editing_content,
    review_scopes_for_changes,
    saved_content,
    workflow_content_digest,
)
from app.services.requirement_workflow import PreparedReview, RequirementWorkflowService

APPROVER_PERMISSIONS = (
    "requirement:read",
    "requirement:approve",
    "artifact_release:read",
)


@dataclass
class PreparedApproval:
    """発行前に確認した条件と、確定する文書全体の本文。"""

    snapshot: PreparedReview
    preview: RequirementFormalApprovalPreview
    basis: RequirementFormalApprovalBasis


class RequirementFormalApprovalService:
    """同一版と指定した承認者の全員承認を、一つの依頼に結び付ける。"""

    def __init__(self, workflow: RequirementWorkflowService) -> None:
        """既存の版固定と案件認可を利用する。"""
        self.workflow = workflow
        self.releases = workflow.releases
        self.repository = workflow.repository

    def _review_evidence(
        self,
        db: Session,
        project_id: int,
        document_id: int,
        prepared: PreparedReview,
        baseline: RequirementWorkflowRequest | None,
    ) -> list[UUID]:
        """文書全体、または開発基準から変更された範囲の同内容を照合する。"""
        snapshot = prepared.snapshot
        attachments = [
            item.model_dump(mode="json") for item in snapshot.request.included_sources
        ]
        attachment_kinds = ("specification", "test_design", "test_design_part")
        expected = [
            digest(node)
            for node in snapshot.nodes
            if node["node_kind"] in attachment_kinds
        ]

        def same_attachments(release_id: UUID, sources: list) -> bool:
            if sources[1:] != attachments:
                return False
            if not expected:
                return True
            node = ArtifactReleaseNode
            return (
                list(
                    db.scalars(
                        select(node.content_digest)
                        .where(
                            node.release_id == release_id,
                            node.node_kind.in_(attachment_kinds),
                        )
                        .order_by(node.ordinal)
                    )
                )
                == expected
            )

        for (
            review_id,
            release_id,
            sources,
        ) in self.repository.completed_document_reviews(
            db, project_id, document_id, prepared.content_digest
        ):
            if same_attachments(release_id, sources):
                return [review_id]
        if baseline is None:
            return []
        release = self.releases.repository.get(db, project_id, baseline.release_id)
        if release is None or not same_attachments(release.id, release.source_refs):
            return []
        node = ArtifactReleaseNode
        baseline_nodes = saved_content(
            db.scalars(
                select(node)
                .where(node.release_id == release.id, node.node_kind.in_(CONTENT_KINDS))
                .order_by(node.ordinal)
            )
        )
        current = editing_content(snapshot.nodes)
        changes = compare_content(baseline_nodes, current)
        if not changes:
            basis = RequirementFormalApprovalBasis.model_validate(
                baseline.approval_basis
            )
            return basis.reviewed_request_ids or (
                [basis.reviewed_request_id] if basis.reviewed_request_id else []
            )
        scopes = review_scopes_for_changes(changes, current)
        if scopes is None:
            return []

        raws = {item["key"]: item for item in snapshot.nodes}
        children: dict[str | None, list[dict]] = defaultdict(list)
        for item in snapshot.nodes:
            children[item["parent"]].append(item)

        def subtree(key: str):
            yield raws[key]
            for child in children[key]:
                yield from subtree(child["key"])

        candidates = dict.fromkeys(
            key for alternatives in scopes for key in alternatives
        )
        keys = {
            key: (
                raws[key]["node_kind"],
                raws[key]["source_id"],
                workflow_content_digest(subtree(key)),
            )
            for key in candidates
        }
        digests = list(keys.values())
        reviews: dict[tuple[str, int, str], UUID] = {}
        for offset in range(0, len(digests), 400):
            matched = self.repository.completed_scoped_reviews(
                db, project_id, document_id, digests[offset : offset + 400]
            )
            reviews.update(
                {
                    (kind, source_id, content_digest): review_id
                    for kind, source_id, content_digest, review_id in matched
                }
            )
        accepted = []
        for alternatives in scopes:
            review_id = next(
                (reviews[keys[key]] for key in alternatives if keys[key] in reviews),
                None,
            )
            if review_id is None:
                return []
            accepted.append(review_id)
        return list(dict.fromkeys(accepted))

    def _can_approve(self, db: Session, actor: User, project_id: int) -> bool:
        return all(
            self.releases.authorization.has_project_permission(
                db, user=actor, project_id=project_id, permission_code=code
            )
            for code in APPROVER_PERMISSIONS
        )

    def candidates(
        self, original: Session, user: User, project_id: int
    ) -> list[RequirementWorkflowRecipient]:
        """文書の承認を依頼できる案件内の候補を返す。"""
        with self.releases._transaction(original, user, project_id, "create") as (
            db,
            actor,
        ):
            self.releases._permission(db, actor, project_id, "requirement:review")
            return [
                RequirementWorkflowRecipient(id=member.id, name=member.name)
                for member in self.repository.recipients(db, project_id)
                if member.id != actor.id and self._can_approve(db, member, project_id)
            ]

    def _prepare(
        self,
        db: Session,
        actor: User,
        project_id: int,
        document_id: int,
        data: RequirementFormalApprovalInput,
        *,
        guest_recipients: tuple[RequirementWorkflowRecipient, ...] = (),
    ) -> PreparedApproval:
        guest_ids = {guest.id for guest in guest_recipients}
        internal_ids = [
            person for person in data.participant_ids if person not in guest_ids
        ]
        approvers = self.repository.recipients(db, project_id, internal_ids)
        if (
            len(approvers) != len(internal_ids)
            or actor.id in data.participant_ids
            or any(
                not self._can_approve(db, member, project_id) for member in approvers
            )
        ):
            raise BadRequestError(error_messages.FIXED_APPROVAL_CANDIDATE_INVALID)
        content = self.workflow._prepare(
            db,
            actor,
            project_id,
            document_id,
            RequirementReviewInput(
                document_version=data.document_version,
                participant_ids=data.participant_ids,
                included_sources=data.included_sources,
            ),
            guest_recipients=guest_recipients,
        )
        snapshot = content.snapshot
        require_review = self.repository.review_required(db, project_id)
        baseline = self.repository.approval_for_document(
            db, project_id, document_id, "approved"
        )
        review_ids = self._review_evidence(
            db, project_id, document_id, content, baseline
        )
        reviewed = review_ids[0] if review_ids else None
        current_blockers = self.repository.approval_blockers(
            db, project_id, document_id
        )
        blocker_keys = {(kind, key) for kind, key, _, _ in current_blockers}
        exception_keys = {(item.kind, item.id) for item in data.exceptions}
        if data.exceptions:
            self.releases._permission(db, actor, project_id, "requirement:approve")
            owner_ids = {item.owner_id for item in data.exceptions}
            active_owners = {
                owner.id
                for owner in self.repository.recipients(
                    db, project_id, sorted(owner_ids)
                )
            }
            if exception_keys - blocker_keys or owner_ids != active_owners:
                raise BadRequestError(
                    error_messages.REQUIREMENT_FORMAL_APPROVAL_INVALID_EXCEPTION
                )
        pending = self.repository.approval_for_document(
            db, project_id, document_id, "requested"
        )
        basis = RequirementFormalApprovalBasis(
            review_required=require_review,
            reviewed_request_id=reviewed,
            reviewed_request_ids=review_ids,
            previous_baseline_id=baseline.id if baseline else None,
            exceptions=data.exceptions,
            exception_versions={
                f"{kind}:{key}": version
                for kind, key, _, version in current_blockers
                if (kind, key) in exception_keys
            },
        )
        confirmation_digest = digest(
            {
                "snapshot": snapshot.scope_digest,
                "input": data.model_dump(mode="json"),
                "basis": basis.model_dump(mode="json"),
                "blockers": current_blockers,
                "pending": str(pending.id) if pending else None,
            }
        )
        preview = RequirementFormalApprovalPreview(
            document_id=document_id,
            document_version=data.document_version,
            title=snapshot.title,
            section_count=sum(
                node["node_kind"] == "section" for node in snapshot.nodes
            ),
            requirement_count=content.preview.requirement_count,
            recipients=content.preview.recipients,
            review_required=require_review,
            reviewed_request_id=reviewed,
            reviewed_request_ids=review_ids,
            blockers=[
                RequirementFormalApprovalBlocker.model_validate(
                    {"kind": kind, "id": key, "title": title}
                )
                for kind, key, title, _ in current_blockers
            ],
            pending_approval_id=pending.id if pending else None,
            previous_baseline_id=basis.previous_baseline_id,
            can_request=(
                pending is None
                and not (blocker_keys - exception_keys)
                and (not require_review or reviewed is not None)
            ),
            confirmation_digest=confirmation_digest,
        )
        return PreparedApproval(content, preview, basis)

    def preview(
        self,
        original: Session,
        user: User,
        project_id: int,
        document_id: int,
        data: RequirementFormalApprovalInput,
    ) -> RequirementFormalApprovalPreview:
        """保存前に、承認対象の件数と解消が必要な条件を確認する。"""
        with self.releases._transaction(original, user, project_id, "create") as (
            db,
            actor,
        ):
            self.releases._permission(db, actor, project_id, "requirement:review")
            return self._prepare(db, actor, project_id, document_id, data).preview

    def create(
        self,
        original: Session,
        user: User,
        project_id: int,
        document_id: int,
        data: RequirementFormalApprovalCreate,
    ) -> tuple[RequirementWorkflowRead, bool]:
        """確認した文書全体、依頼、承認者、申請条件をまとめて保存する。"""
        payload_digest = digest(
            {
                "kind": "formal_approval",
                "document_id": document_id,
                "request": data.model_dump(mode="json"),
            }
        )
        preview_input = RequirementFormalApprovalInput.model_validate(
            data.model_dump(
                exclude={"request_key", "expected_digest", "confirmed_content"}
            )
        )
        for attempt in range(3):
            try:
                with self.releases._transaction(
                    original, user, project_id, "create"
                ) as (db, actor):
                    self.releases._permission(
                        db, actor, project_id, "requirement:review"
                    )
                    previous = self.repository.by_key(
                        db, project_id, actor.id, data.request_key
                    )
                    if previous is not None:
                        if previous.request_digest != payload_digest:
                            raise DuplicateResourceError()
                        self.releases._get(db, actor, project_id, previous.release_id)
                        return self.workflow._reads(db, [previous])[0], False
                    prepared = self._prepare(
                        db, actor, project_id, document_id, preview_input
                    )
                    result = self._save(
                        db,
                        actor,
                        project_id,
                        document_id,
                        data,
                        prepared,
                        payload_digest,
                    )
                return result, True
            except DBAPIError as exc:
                code = getattr(exc.orig, "pgcode", None)
                constraint = getattr(
                    getattr(exc.orig, "diag", None), "constraint_name", None
                )
                if code not in {"40001", "40P01"} and not (
                    code == "23505"
                    and constraint
                    in {
                        "uq_requirement_workflow_pending_approval",
                        "uq_requirement_workflow_request_key",
                        "uq_requirement_workflow_display_number",
                        "uq_artifact_release_request",
                    }
                ):
                    raise
                if attempt == 2:
                    raise ConflictError(
                        error_messages.ARTIFACT_RETRY_CONFLICT
                    ) from None
        raise AssertionError("unreachable")

    def _save(
        self,
        db: Session,
        actor: User,
        project_id: int,
        document_id: int,
        data: RequirementFormalApprovalCreate,
        prepared: PreparedApproval,
        payload_digest: str,
    ) -> RequirementWorkflowRead:
        self._check_submission(prepared.preview, data.expected_digest)

        snapshot = prepared.snapshot.snapshot
        release = self.releases.save_snapshot(
            db,
            actor,
            project_id,
            ArtifactCreateRequest(
                **snapshot.request.model_dump(),
                request_id=uuid5(
                    data.request_key, "requirement-formal-approval-release"
                ),
                expected_scope_digest=snapshot.scope_digest,
            ),
            snapshot,
        )
        row = self._save_request(
            db,
            actor,
            project_id,
            document_id,
            data,
            prepared,
            payload_digest,
            release_id=release.id,
            internal_ids=data.participant_ids,
        )
        return self.workflow._reads(db, [row])[0]

    @staticmethod
    def _check_submission(
        preview: RequirementFormalApprovalPreview, expected_digest: str
    ):
        if preview.pending_approval_id is not None:
            raise ConflictError(error_messages.REQUIREMENT_FORMAL_APPROVAL_PENDING)
        if preview.confirmation_digest != expected_digest:
            raise VersionConflictError(
                {"confirmation_digest": preview.confirmation_digest}
            )
        if preview.review_required and preview.reviewed_request_id is None:
            raise ConflictError(
                error_messages.REQUIREMENT_FORMAL_APPROVAL_REVIEW_REQUIRED
            )
        if not preview.can_request:
            raise ConflictError(error_messages.REQUIREMENT_FORMAL_APPROVAL_BLOCKED)

    def _save_request(
        self,
        db: Session,
        actor: User,
        project_id: int,
        document_id: int,
        data: RequirementFormalApprovalCreate,
        prepared: PreparedApproval,
        payload_digest: str,
        *,
        release_id: UUID,
        internal_ids: list[int],
    ) -> RequirementWorkflowRequest:
        row = RequirementWorkflowRequest(
            project_id=project_id,
            document_id=document_id,
            release_id=release_id,
            target_node_id=uuid5(release_id, prepared.snapshot.target_key),
            scope_kind="requirement_document",
            scope_id=document_id,
            kind="formal_approval",
            requester_id=actor.id,
            request_key=data.request_key,
            request_digest=payload_digest,
            content_digest=prepared.snapshot.content_digest,
            approval_basis=prepared.basis.model_dump(mode="json"),
            note=data.note or None,
            due_on=data.due_on,
        )
        self.repository.create(db, row, internal_ids)
        self._audit(db, actor, row, "requested")
        return row

    def _audit(
        self, db: Session, actor: User, row: RequirementWorkflowRequest, action: str
    ) -> None:
        AuditLogService().record(
            db,
            event_type=f"requirement_workflow.formal_approval_{action}",
            tenant_id=db.info.get("tenant_id")
            or db.scalar(select(Project.tenant_id).where(Project.id == row.project_id)),
            actor_user_id=actor.id,
            project_id=row.project_id,
            resource_type="requirement_workflow",
            metadata={
                "request_id": str(row.id),
                "release_id": str(row.release_id),
                "status": row.status,
            },
            commit=False,
        )

    def _approval(
        self,
        db: Session,
        actor: User,
        project_id: int,
        request_id: UUID,
        *,
        lock: bool = False,
    ) -> RequirementWorkflowRequest:
        row = self.workflow.readable_content_request(
            db, actor, project_id, request_id, lock=lock
        )
        if row.kind != "formal_approval":
            raise NotFoundError()
        return row

    def decision_preview(
        self, original: Session, user: User, project_id: int, request_id: UUID
    ) -> RequirementFormalApprovalDecisionPreview:
        """対象版と比較できる前回の開発基準を提示する。"""
        with self.releases._transaction(original, user, project_id, None) as (
            db,
            actor,
        ):
            row = self._approval(db, actor, project_id, request_id)
            release = self.releases._get(db, actor, project_id, row.release_id)
            basis = RequirementFormalApprovalBasis.model_validate(row.approval_basis)
            previous = None
            if basis.previous_baseline_id is not None:
                try:
                    baseline = self._approval(
                        db, actor, project_id, basis.previous_baseline_id
                    )
                except ForbiddenError:
                    pass
                else:
                    previous = self.workflow._reads(db, [baseline])[0]
            participant = db.get(RequirementWorkflowParticipant, (row.id, actor.id))
            return RequirementFormalApprovalDecisionPreview(
                request=self.workflow._reads(db, [row])[0],
                previous_baseline=previous,
                release_digest=release.content_digest,
                can_decide=(
                    row.status == "requested"
                    and participant is not None
                    and participant.status == "pending"
                    and self._can_approve(db, actor, project_id)
                ),
            )

    def decide(
        self,
        original: Session,
        user: User,
        project_id: int,
        request_id: UUID,
        data: RequirementFormalApprovalDecisionInput,
    ) -> RequirementWorkflowRead:
        """承認者本人の判断を記録し、全員の承認で開発基準を確定する。"""
        try:
            with self.releases._transaction(original, user, project_id, None) as (
                db,
                actor,
            ):
                row = self._approval(db, actor, project_id, request_id, lock=True)
                if not self._can_approve(db, actor, project_id):
                    raise ForbiddenError()
                participant = db.get(RequirementWorkflowParticipant, (row.id, actor.id))
                if participant is None:
                    raise ForbiddenError()
                release = self.releases._get(db, actor, project_id, row.release_id)
                if release.content_digest != data.expected_release_digest:
                    raise VersionConflictError({"version": row.version})
                self._apply_decision(db, actor, row, participant, data)
                return self.workflow._reads(db, [row])[0]
        except DBAPIError as exc:
            if getattr(exc.orig, "pgcode", None) in {"40001", "40P01"}:
                raise ConflictError(error_messages.ARTIFACT_RETRY_CONFLICT) from None
            raise

    def _apply_decision(
        self,
        db: Session,
        actor: User,
        row: RequirementWorkflowRequest,
        participant: RequirementWorkflowParticipant,
        data: RequirementFormalApprovalDecision,
    ) -> None:
        if participant.operation_key == data.operation_key:
            if participant.status != data.decision or participant.response_note != (
                data.reason or None
            ):
                raise DuplicateResourceError()
            return
        if row.status != "requested" or participant.status != "pending":
            raise ConflictError(error_messages.REQUIREMENT_FORMAL_APPROVAL_CLOSED)
        if data.version != row.version:
            raise VersionConflictError({"version": row.version, "status": row.status})
        if data.decision == "approved":
            basis = RequirementFormalApprovalBasis.model_validate(row.approval_basis)
            blockers = self.repository.approval_blockers(
                db, row.project_id, row.document_id
            )
            if any(
                basis.exception_versions.get(f"{kind}:{key}") != version
                for kind, key, _, version in blockers
            ):
                raise ConflictError(error_messages.REQUIREMENT_FORMAL_APPROVAL_BLOCKED)
        now = datetime.now(UTC)
        participant.status = data.decision
        participant.response_note = data.reason or None
        participant.operation_key = data.operation_key
        participant.responded_at = now
        db.flush()
        _, participants, _ = self.repository.display_data(db, [row])
        if data.decision == "returned":
            row.status = "returned"
        elif all(person.status == "approved" for person in participants[row.id]):
            row.status = "approved"
        if row.status != "requested":
            row.finalized_at = now
        row.version += 1
        db.flush()
        self._audit(db, actor, row, data.decision)

    def baseline(
        self, original: Session, user: User, project_id: int, document_id: int
    ) -> RequirementWorkflowRead | None:
        """現在の開発基準として、最後に正式承認された文書全体を返す。"""
        with self.releases._transaction(original, user, project_id, None) as (
            db,
            actor,
        ):
            self.releases._permission(db, actor, project_id, "requirement:read")
            row = self.repository.approval_for_document(
                db, project_id, document_id, "approved"
            )
            if row is None:
                return None
            self._approval(db, actor, project_id, row.id)
            return self.workflow._reads(db, [row])[0]
