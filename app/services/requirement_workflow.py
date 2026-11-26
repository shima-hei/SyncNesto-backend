"""固定版の確認とレビュー依頼の発行を、同じ案件境界で管理する。"""

from collections import Counter
from dataclasses import dataclass
from uuid import UUID, uuid5

from sqlalchemy import func, or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import (
    ArtifactReleaseTooLargeError,
    BadRequestError,
    ConflictError,
    DuplicateResourceError,
    NotFoundError,
    VersionConflictError,
)
from app.models.artifact_release import ArtifactRelease, ArtifactReleaseNode
from app.models.requirement import RequirementDocument
from app.models.requirement_workflow import (
    RequirementWorkflowGuestBinding,
    RequirementWorkflowParticipant,
    RequirementWorkflowRequest,
)
from app.models.user import User
from app.repositories.requirement_workflow import RequirementWorkflowRepository
from app.schemas.artifact_release import (
    ArtifactCreateRequest,
    ArtifactNodePage,
    ArtifactNodeRead,
    ArtifactPreviewRequest,
    ArtifactSource,
)
from app.schemas.requirement_workflow import (
    RequirementBaselineComparison,
    RequirementManagementCorrection,
    RequirementReviewInput,
    RequirementReviewManagementRead,
    RequirementReviewPreview,
    RequirementReviewRequestCreate,
    RequirementWorkflowList,
    RequirementWorkflowParticipantRead,
    RequirementWorkflowRead,
    RequirementWorkflowRecipient,
    WorkflowKind,
    WorkflowListView,
    WorkflowStatus,
)
from app.services.artifact_release import SOURCE_PERMISSIONS, ArtifactReleaseService
from app.services.artifact_release_cursor import decode_cursor, encode_cursor
from app.services.artifact_release_snapshot import MAX_PAGE_BYTES, Snapshot, digest
from app.services.audit_log import AuditLogService
from app.services.requirement_baseline_changes import (
    CONTENT_KINDS,
    compare_content,
    editing_content,
    saved_content,
    workflow_content_digest,
)


@dataclass
class PreparedReview:
    """確認した画面情報と、その場で保存できる固定版本文。"""

    preview: RequirementReviewPreview
    snapshot: Snapshot
    target_key: str
    content_digest: str


class RequirementWorkflowService:
    """既存の成果物固定版をレビュー依頼から使える形にする。"""

    def __init__(self) -> None:
        """固定版サービスと依頼リポジトリを準備する。"""
        self.releases = ArtifactReleaseService()
        self.repository = RequirementWorkflowRepository()

    def _candidates(
        self, db: Session, project_id: int, data: RequirementReviewInput | None
    ) -> list[User]:
        """現在の権限で対象を読める社内メンバーを返す。"""
        required = {"requirement:read"}
        if data is not None:
            required.update(
                f"{SOURCE_PERMISSIONS[source.kind]}:read"
                for source in data.included_sources
            )
        return [
            member
            for member in self.repository.recipients(
                db, project_id, data.participant_ids if data is not None else None
            )
            if all(
                self.releases.authorization.has_project_permission(
                    db, user=member, project_id=project_id, permission_code=code
                )
                for code in required
            )
        ]

    def candidates(self, db: Session, user: User, project_id: int):
        """レビューを依頼できるメンバーを発行権限の範囲で取得する。"""
        with self.releases._transaction(db, user, project_id, "create") as (
            work,
            actor,
        ):
            self.releases._permission(work, actor, project_id, "requirement:review")
            return [
                RequirementWorkflowRecipient(id=member.id, name=member.name)
                for member in self._candidates(work, project_id, None)
            ]

    def _prepare(
        self,
        db: Session,
        actor: User,
        project_id: int,
        document_id: int,
        data: RequirementReviewInput,
        *,
        guest_recipients: tuple[RequirementWorkflowRecipient, ...] = (),
    ) -> PreparedReview:
        """指定範囲の固定対象・担当者・確認用ハッシュを算出する。"""
        candidates = self._candidates(db, project_id, data)
        recipients = [
            RequirementWorkflowRecipient(id=member.id, name=member.name)
            for member in candidates
        ]
        recipients.extend(guest_recipients)
        if set(data.participant_ids) != {member.id for member in recipients}:
            raise BadRequestError(
                error_messages.REQUIREMENT_WORKFLOW_INVALID_PARTICIPANT
            )
        if len({(item.kind, item.id) for item in data.included_sources}) != len(
            data.included_sources
        ):
            raise BadRequestError(
                error_messages.REQUIREMENT_WORKFLOW_DUPLICATE_ATTACHMENT
            )

        request = ArtifactPreviewRequest(
            source=ArtifactSource(
                kind="requirement_document",
                id=document_id,
                version=data.document_version,
            ),
            included_sources=data.included_sources,
            projection_version="internal-preview-v2",
        )
        snapshot = self.releases.capture_snapshot(db, actor, project_id, request)
        scope_id = data.scope_id if data.scope_id is not None else document_id
        target = next(
            (
                node
                for node in snapshot.nodes
                if node["node_kind"] == data.scope_kind
                and node["source_id"] == scope_id
            ),
            None,
        )
        if target is None:
            raise NotFoundError()

        target_keys = {target["key"]}
        scope_nodes = []
        for node in snapshot.nodes:
            if node["key"] != target["key"] and node["parent"] not in target_keys:
                continue
            target_keys.add(node["key"])
            scope_nodes.append(node)

        # 版番号・運用状態の変更と、確認対象本文の変更を区別する。
        content_digest = workflow_content_digest(scope_nodes)
        confirmation_digest = digest(
            {"snapshot": snapshot.scope_digest, "review": data.model_dump(mode="json")}
        )
        return PreparedReview(
            preview=RequirementReviewPreview(
                document_id=document_id,
                document_version=data.document_version,
                title=snapshot.title,
                target_title=target["payload"]["title"],
                scope_kind=data.scope_kind,
                scope_id=scope_id,
                requirement_count=sum(
                    node["node_kind"] == "requirement" for node in scope_nodes
                ),
                due_on=data.due_on,
                recipients=recipients,
                confirmation_digest=confirmation_digest,
            ),
            snapshot=snapshot,
            target_key=target["key"],
            content_digest=content_digest,
        )

    def preview(
        self,
        db: Session,
        user: User,
        project_id: int,
        document_id: int,
        data: RequirementReviewInput,
    ) -> RequirementReviewPreview:
        """保存前に、依頼する範囲と担当者を確認する。"""
        with self.releases._transaction(db, user, project_id, "create") as (
            work,
            actor,
        ):
            self.releases._permission(work, actor, project_id, "requirement:review")
            return self._prepare(work, actor, project_id, document_id, data).preview

    def create(
        self,
        db: Session,
        user: User,
        project_id: int,
        document_id: int,
        data: RequirementReviewRequestCreate,
    ) -> tuple[RequirementWorkflowRead, bool]:
        """固定版・依頼・担当者を一括保存し、同じ依頼の再送を処理する。"""
        payload_digest = digest(
            {"document_id": document_id, "request": data.model_dump(mode="json")}
        )
        preview_input = RequirementReviewInput.model_validate(
            data.model_dump(exclude={"request_key", "expected_digest"})
        )
        for attempt in range(3):
            try:
                with self.releases._transaction(db, user, project_id, "create") as (
                    work,
                    actor,
                ):
                    self.releases._permission(
                        work, actor, project_id, "requirement:review"
                    )
                    previous = self.repository.by_key(
                        work, project_id, actor.id, data.request_key
                    )
                    if previous is not None:
                        if previous.request_digest != payload_digest:
                            raise DuplicateResourceError()
                        return self._reads(work, [previous])[0], False

                    prepared = self._prepare(
                        work, actor, project_id, document_id, preview_input
                    )
                    confirmed = prepared.preview.confirmation_digest
                    if confirmed != data.expected_digest:
                        raise VersionConflictError({"confirmation_digest": confirmed})
                    result = self._create_review(
                        work, actor, project_id, data, prepared, payload_digest
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
                        "uq_artifact_release_request",
                        "uq_requirement_workflow_request_key",
                        "uq_requirement_workflow_display_number",
                    }
                ):
                    raise
                if attempt == 2:
                    raise ConflictError(
                        error_messages.ARTIFACT_RETRY_CONFLICT
                    ) from None
        raise AssertionError("unreachable")

    def _create_review(
        self,
        db: Session,
        actor: User,
        project_id: int,
        data: RequirementReviewRequestCreate,
        prepared: PreparedReview,
        payload_digest: str,
    ) -> RequirementWorkflowRead:
        """検証済みの入力を、外部送信を伴わないDBトランザクションへ保存する。"""
        snapshot = prepared.snapshot
        release = self.releases.save_snapshot(
            db,
            actor,
            project_id,
            ArtifactCreateRequest(
                **snapshot.request.model_dump(),
                request_id=uuid5(data.request_key, "requirement-workflow-release"),
                expected_scope_digest=snapshot.scope_digest,
            ),
            snapshot,
        )
        row = RequirementWorkflowRequest(
            project_id=project_id,
            document_id=prepared.preview.document_id,
            release_id=release.id,
            target_node_id=uuid5(release.id, prepared.target_key),
            scope_kind=prepared.preview.scope_kind,
            scope_id=prepared.preview.scope_id,
            kind="review",
            requester_id=actor.id,
            request_key=data.request_key,
            request_digest=payload_digest,
            content_digest=prepared.content_digest,
            note=data.note or None,
            due_on=data.due_on,
        )
        self.repository.create(db, row, data.participant_ids)
        AuditLogService().record(
            db,
            event_type="requirement_workflow.review_requested",
            actor_user_id=actor.id,
            project_id=project_id,
            resource_type="requirement_workflow",
            metadata={"request_id": str(row.id), "release_id": str(release.id)},
            commit=False,
        )
        return self._reads(db, [row])[0]

    def _reads(self, db: Session, entries: list[RequirementWorkflowRequest]):
        """固定版と担当者を表示用に結合する。"""
        versions, people, names = self.repository.display_data(db, entries)
        binding = RequirementWorkflowGuestBinding
        external = (
            set(
                db.scalars(
                    select(binding.request_id).where(
                        binding.request_id.in_([row.id for row in entries])
                    )
                )
            )
            if entries
            else set()
        )
        result = []
        for entry in entries:
            title, document_version, target_title = versions[entry.id]
            version_prefix = "R" if entry.kind == "review" else "A"
            result.append(
                RequirementWorkflowRead.model_validate(
                    {
                        "id": entry.id,
                        "project_id": entry.project_id,
                        "document_id": entry.document_id,
                        "release_id": entry.release_id,
                        "target_node_id": entry.target_node_id,
                        "document_version": document_version,
                        "version_label": f"{version_prefix}-{entry.display_number:03d}",
                        "title": title,
                        "target_title": target_title,
                        "scope_kind": entry.scope_kind,
                        "scope_id": entry.scope_id,
                        "kind": entry.kind,
                        "status": entry.status,
                        "requester_id": entry.requester_id,
                        "requester_name": names.get(entry.requester_id),
                        "note": entry.note,
                        "due_on": entry.due_on,
                        "version": entry.version,
                        "created_at": entry.created_at,
                        "finalized_at": entry.finalized_at,
                        "approval_basis": entry.approval_basis,
                        "external_review": entry.kind == "review"
                        and entry.id in external
                        and entry.kind == "review",
                        "participants": [
                            RequirementWorkflowParticipantRead.model_validate(
                                {
                                    "user_id": participant.user_id,
                                    "name": names.get(participant.user_id),
                                    "status": participant.status,
                                    "response_note": participant.response_note,
                                    "responded_at": participant.responded_at,
                                }
                            )
                            for participant in people.get(entry.id, [])
                        ],
                    }
                )
            )
        return result

    def get(self, db: Session, user: User, project_id: int, request_id: UUID):
        """案件内の依頼と固定版の表示情報を取得する。"""
        with self.releases._transaction(db, user, project_id, None) as (work, actor):
            self.releases._permission(work, actor, project_id, "requirement:read")
            row = self.repository.get(work, project_id, request_id)
            if row is None:
                raise NotFoundError()
            return self._reads(work, [row])[0]

    def readable_content_request(
        self,
        db: Session,
        actor: User,
        project_id: int,
        request_id: UUID,
        *,
        lock: bool = False,
    ) -> RequirementWorkflowRequest:
        """現在の案件・固定版の閲覧権限を確認する。"""
        self.releases._permission(db, actor, project_id, "requirement:read")
        row = self.repository.get(db, project_id, request_id, lock=lock)
        if row is None:
            raise NotFoundError()
        if row.requester_id != actor.id and not self.repository.is_assigned(
            db, row.id, actor.id
        ):
            self.releases._permission(db, actor, project_id, "artifact_release:read")
        self.releases._get(db, actor, project_id, row.release_id)
        return row

    def content(
        self,
        db: Session,
        user: User,
        project_id: int,
        request_id: UUID,
        cursor: str | None,
        page_size: int,
        *,
        node_id: UUID | None = None,
    ) -> ArtifactNodePage:
        """案件と依頼に結び付けたcursorで、指定された固定内容だけを返す。"""
        with self.releases._transaction(db, user, project_id, None) as (work, actor):
            row = self.readable_content_request(work, actor, project_id, request_id)
            scoped = self.repository.scoped_nodes(row)
            if node_id is not None:
                if cursor is not None:
                    raise BadRequestError()
                candidate = work.scalar(scoped.where(ArtifactReleaseNode.id == node_id))
                if candidate is None:
                    raise NotFoundError()
                item = ArtifactNodeRead.model_validate(candidate)
                if item.id == row.target_node_id:
                    item = item.model_copy(update={"parent_id": None})
                result = ArtifactNodePage(
                    release_id=row.release_id, items=[item], total=1, next_cursor=None
                )
                if len(result.model_dump_json().encode()) > MAX_PAGE_BYTES:
                    raise ArtifactReleaseTooLargeError()
                return result
            after = decode_cursor(cursor, project_id, row.id) if cursor else -1
            total = (
                work.scalar(select(func.count()).select_from(scoped.subquery())) or 0
            )
            candidates = list(
                work.scalars(
                    scoped.where(ArtifactReleaseNode.ordinal > after)
                    .order_by(ArtifactReleaseNode.ordinal)
                    .limit(page_size + 1)
                )
            )
            result = ArtifactNodePage(
                release_id=row.release_id, items=[], total=total, next_cursor=None
            )
            for index, candidate in enumerate(candidates[:page_size]):
                item = ArtifactNodeRead.model_validate(candidate)
                if item.id == row.target_node_id:
                    item = item.model_copy(update={"parent_id": None})
                next_cursor = (
                    encode_cursor(project_id, row.id, item.ordinal)
                    if index + 1 < len(candidates)
                    else None
                )
                proposed = result.model_copy(
                    update={"items": [*result.items, item], "next_cursor": next_cursor}
                )
                if len(proposed.model_dump_json().encode()) > MAX_PAGE_BYTES:
                    if not result.items:
                        raise ArtifactReleaseTooLargeError()
                    break
                result = proposed
            return result

    @staticmethod
    def _document(
        db: Session, project_id: int, document_id: int
    ) -> RequirementDocument:
        document = db.scalar(
            select(RequirementDocument).where(
                RequirementDocument.id == document_id,
                RequirementDocument.project_id == project_id,
                RequirementDocument.deleted_at.is_(None),
            )
        )
        if document is None:
            raise NotFoundError()
        return document

    def baseline_comparison(
        self,
        db: Session,
        user: User,
        project_id: int,
        document_id: int,
        page: int,
        page_size: int,
    ) -> RequirementBaselineComparison | None:
        """保存した編集内容と、閲覧できる最新の正式承認版を照合する。"""
        with self.releases._transaction(db, user, project_id, None) as (work, actor):
            self.releases._permission(work, actor, project_id, "requirement:read")
            document = self._document(work, project_id, document_id)
            baseline = self.repository.approval_for_document(
                work, project_id, document_id, "approved"
            )
            if baseline is None:
                return None
            self.readable_content_request(work, actor, project_id, baseline.id)
            node = ArtifactReleaseNode
            before = saved_content(
                work.scalars(
                    select(node)
                    .where(
                        node.release_id == baseline.release_id,
                        node.node_kind.in_(CONTENT_KINDS),
                    )
                    .order_by(node.ordinal)
                )
            )
            after = editing_content(
                self.releases._build_snapshot(
                    work,
                    project_id,
                    ArtifactPreviewRequest(
                        source=ArtifactSource(
                            kind="requirement_document",
                            id=document.id,
                            version=document.version,
                        ),
                        projection_version="internal-preview-v2",
                    ),
                ).nodes
            )
            changes = compare_content(before, after)
            counts = Counter(
                item.change for item in changes if item.kind == "requirement"
            )
            offset = (page - 1) * page_size
            return RequirementBaselineComparison(
                baseline_id=baseline.id,
                baseline_label=f"A-{baseline.display_number:03d}",
                baseline_count=sum(item.kind == "requirement" for item in before),
                current_count=sum(item.kind == "requirement" for item in after),
                requirement_added=counts["added"],
                requirement_changed=counts["changed"],
                requirement_removed=counts["removed"],
                items=changes[offset : offset + page_size],
                total=len(changes),
                page=page,
                page_size=page_size,
            )

    def management(
        self,
        db: Session,
        user: User,
        project_id: int,
        document_id: int,
        resolved: bool | None,
        page: int,
        page_size: int,
    ) -> RequirementReviewManagementRead:
        """定義書内で現在の権限で閲覧できる進捗と対応項目を集計する。"""
        with self.releases._transaction(db, user, project_id, None) as (work, actor):
            self.releases._permission(work, actor, project_id, "requirement:read")
            self._document(work, project_id, document_id)

            def permits(code: str) -> bool:
                return self.releases.authorization.has_project_permission(
                    work, user=actor, project_id=project_id, permission_code=code
                )

            readable_kinds = [
                kind
                for kind, prefix in SOURCE_PERMISSIONS.items()
                if permits(f"{prefix}:read")
            ]
            released = self.releases.repository.visible(
                project_id, readable_kinds
            ).with_only_columns(ArtifactRelease.id)
            row = RequirementWorkflowRequest
            readable = select(row.id).where(
                row.project_id == project_id,
                row.document_id == document_id,
                row.release_id.in_(released),
            )
            if not permits("artifact_release:read"):
                participant = RequirementWorkflowParticipant
                readable = readable.where(
                    or_(
                        row.requester_id == actor.id,
                        select(participant.request_id)
                        .where(
                            participant.request_id == row.id,
                            participant.user_id == actor.id,
                        )
                        .exists(),
                    )
                )
            review, active, formal = self.repository.management_requests(
                work, project_id, document_id, readable
            )
            requests = {
                item.kind: item
                for item in self._reads(
                    work, [item for item in (review, formal) if item is not None]
                )
            }
            corrections, total, (open_corrections, resolved_corrections) = (
                self.repository.management_corrections(
                    work, project_id, document_id, readable, resolved, page, page_size
                )
            )
            open_issues, resolved_issues, blocking_issues = (
                self.repository.management_issue_counts(work, document_id)
            )
            return RequirementReviewManagementRead(
                current_review=requests.get("review"),
                active_review_count=active,
                formal_approval=requests.get("formal_approval"),
                corrections=[
                    RequirementManagementCorrection.model_validate(
                        dict(
                            correction,
                            version_label=f"R-{correction.display_number:03d}",
                        )
                    )
                    for correction in corrections
                ],
                correction_total=total,
                open_correction_count=open_corrections,
                resolved_correction_count=resolved_corrections,
                open_issue_count=open_issues,
                resolved_issue_count=resolved_issues,
                blocking_issue_count=blocking_issues,
            )

    def list(
        self,
        db: Session,
        user: User,
        project_id: int,
        document_id: int | None,
        view: WorkflowListView,
        page: int,
        page_size: int,
        *,
        kind: WorkflowKind | None = None,
        status: WorkflowStatus | None = None,
        search: str | None = None,
        requirement_id: int | None = None,
    ) -> RequirementWorkflowList:
        """案件内または定義書内の依頼をページ取得する。"""
        with self.releases._transaction(db, user, project_id, None) as (work, actor):
            self.releases._permission(work, actor, project_id, "requirement:read")
            visible_releases = None
            can_read_releases = False
            if requirement_id is not None:
                authorization = self.releases.authorization
                permissions = {
                    prefix: authorization.has_project_permission(
                        work,
                        user=actor,
                        project_id=project_id,
                        permission_code=f"{prefix}:read",
                    )
                    for prefix in {*SOURCE_PERMISSIONS.values(), "artifact_release"}
                }
                visible_releases = self.releases.repository.visible(
                    project_id,
                    [
                        source
                        for source, prefix in SOURCE_PERMISSIONS.items()
                        if permissions[prefix]
                    ],
                ).with_only_columns(ArtifactRelease.id)
                can_read_releases = permissions["artifact_release"]
            entries, total = self.repository.page(
                work,
                project_id,
                document_id,
                actor.id,
                view,
                page,
                page_size,
                kind=kind,
                status=status,
                search=search,
                requirement_id=requirement_id,
                visible_releases=visible_releases,
                can_read_releases=can_read_releases,
            )
            return RequirementWorkflowList(
                items=self._reads(work, entries),
                total=total,
                page=page,
                page_size=page_size,
            )
