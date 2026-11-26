"""公開された固定範囲と、指定された本人の要件レビューを結ぶ。"""

from contextlib import contextmanager
from uuid import UUID, uuid5

from sqlalchemy import and_, func, insert, or_, select, text

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    DuplicateResourceError,
    ForbiddenError,
    NotFoundError,
    VersionConflictError,
)
from app.db.base import Base
from app.models.artifact_publication import ArtifactPublicationNode
from app.models.artifact_release import ArtifactReleaseNode
from app.models.project import Project
from app.models.requirement_workflow import (
    RequirementReviewThreadEvent,
    RequirementWorkflowGuestBinding,
    RequirementWorkflowParticipant,
    RequirementWorkflowRequest,
)
from app.schemas.artifact_publication import PublicationNodeRead
from app.schemas.artifact_release import ArtifactPreviewRequest, ArtifactSource
from app.schemas.guest_requirement_workflow import (
    GuestRequirementFormalApprovalCreate,
    GuestRequirementReviewOptions,
    SharedRequirementApprovalChange,
    SharedRequirementApprovalComparison,
    SharedRequirementFormalApprovalRead,
    SharedRequirementReviewList,
    SharedRequirementReviewNodes,
    SharedRequirementReviewRead,
    SharedRequirementReviewReplies,
    SharedRequirementReviewSummary,
    SharedRequirementReviewThread,
    SharedRequirementReviewThreads,
)
from app.schemas.requirement_workflow import (
    RequirementFormalApprovalBasis,
    RequirementFormalApprovalCreate,
    RequirementFormalApprovalInput,
    RequirementWorkflowRecipient,
)
from app.services.artifact_release_snapshot import MAX_PAGE_BYTES, digest
from app.services.audit_log import AuditLogService
from app.services.fixed_approval_cursor import encode_cursor
from app.services.guest_fixed_approval import GuestFixedApprovalService
from app.services.publication_access_context import retry_publication_operation
from app.services.requirement_baseline_changes import (
    CONTENT_KINDS,
    ContentNode,
    compare_content,
    workflow_content_digest,
)
from app.services.requirement_formal_approval import RequirementFormalApprovalService
from app.services.requirement_workflow import RequirementWorkflowService
from app.services.requirement_workflow_review import RequirementWorkflowReviewService
from app.services.shared_fixed_approval import SharedFixedApprovalService
from app.services.shared_publication import SharedPublicationService

REVIEW_TARGETS = ("requirement_document", "section", "requirement")
ATTACHMENT_ROOTS = ("specification", "test_design")


class GuestRequirementWorkflowService:
    """公開範囲でのレビューを、内部と同じ依頼・参加者・確認へ保存する。"""

    def __init__(self):
        self.workflow = RequirementWorkflowService()
        self.review = RequirementWorkflowReviewService(self.workflow)
        self.formal = RequirementFormalApprovalService(self.workflow)
        self.shared = SharedPublicationService()
        self.recipients = GuestFixedApprovalService()

    def _source(self, db, actor, project_id, publication_id):
        self.shared.context.permission(
            db, actor, project_id, "artifact_publication:publish"
        )
        self.shared.context.permission(db, actor, project_id, "requirement:review")
        self.shared.context.permission(db, actor, project_id, "artifact_release:read")
        publication = self.shared.repository.publication(db, project_id, publication_id)
        if publication is None or publication.publication_state != "published":
            raise NotFoundError()
        release = self.workflow.releases._get(
            db, actor, project_id, publication.release_id
        )
        attachments = release.source_refs[1:]
        if (
            release.source_kind != "requirement_document"
            or len(attachments) > 20
            or any(item["kind"] not in ATTACHMENT_ROOTS for item in attachments)
        ):
            raise BadRequestError(
                "要件定義書と、最大20件の設計・テスト設計を含む公開版を指定してください。"
            )
        return publication, release

    @retry_publication_operation
    def options(self, original, user, project_id, publication_id):
        with self.shared.context.transaction(original, user, project_id) as (db, actor):
            publication, _ = self._source(db, actor, project_id, publication_id)
            return GuestRequirementReviewOptions.model_validate(
                {
                    "public_digest": publication.public_digest,
                    "reviewers": [
                        dict(row)
                        for row in self.recipients.recipients(
                            db, actor, project_id, publication.id
                        )
                    ],
                }
            )

    def _related_users(self, original, project_id, data):
        grants, participants = (
            self.shared.repository.grants,
            self.shared.repository.participations,
        )
        with original.get_bind().engine.connect() as connection:
            return tuple(
                connection.scalars(
                    select(participants.c.user_id)
                    .join(grants, grants.c.participation_id == participants.c.id)
                    .where(
                        grants.c.project_id == project_id,
                        grants.c.publication_id == data.publication_id,
                        grants.c.id.in_(data.grant_ids),
                    )
                )
            )

    @retry_publication_operation
    def create(self, original, user, project_id, data):
        related = self._related_users(original, project_id, data)
        with self.shared.context.transaction(
            original, user, project_id, write=True, related_user_ids=related
        ) as (db, actor):
            publication, release = self._source(
                db, actor, project_id, data.publication_id
            )
            request_digest = digest({"guest_review": data.model_dump(mode="json")})
            prior = self.workflow.repository.by_key(
                db, project_id, actor.id, data.request_key
            )
            if prior is not None:
                if prior.request_digest != request_digest:
                    raise DuplicateResourceError()
                return self.workflow._reads(db, [prior])[0], False
            if publication.public_digest != data.expected_public_digest:
                raise VersionConflictError({"public_digest": publication.public_digest})
            recipients = self._selected_recipients(
                db, actor, project_id, publication.id, data.grant_ids
            )
            self._check_locked_users(recipients, related)
            mapped = publication.source_manifest.get("id_map", {}).get(
                str(data.target_node_id)
            )
            if mapped is None:
                raise NotFoundError()
            document = self.workflow._document(db, project_id, release.source_id)
            db.refresh(document, with_for_update=True)
            if document.deleted_at is not None:
                raise NotFoundError()
            current = self.workflow.releases._build_snapshot(
                db,
                project_id,
                ArtifactPreviewRequest.model_validate(
                    {
                        "source": release.source_refs[0],
                        "included_sources": release.source_refs[1:],
                        "projection_version": release.projection_version,
                    }
                ),
            )
            if current.scope_digest != release.content_digest:
                raise ConflictError(
                    "対象が更新されています。最新の版を共有してからレビューを依頼してください。"
                )
            nodes = list(
                db.scalars(
                    select(ArtifactReleaseNode)
                    .where(ArtifactReleaseNode.release_id == release.id)
                    .order_by(ArtifactReleaseNode.ordinal)
                )
            )
            target = next((node for node in nodes if str(node.id) == mapped), None)
            if target is None or target.node_kind not in REVIEW_TARGETS:
                raise NotFoundError()
            keys = {node.id: f"{node.node_kind}:{node.source_id}" for node in nodes}
            scoped = {target.id}
            subject = []
            for node in nodes:
                if node.id == target.id or node.parent_id in scoped:
                    scoped.add(node.id)
                    subject.append(
                        dict(
                            node_kind=node.node_kind,
                            source_id=node.source_id,
                            parent=keys[node.parent_id]
                            if node.parent_id is not None
                            else None,
                            position=node.position,
                            payload=node.payload,
                        )
                    )
            request = RequirementWorkflowRequest(
                project_id=project_id,
                document_id=document.id,
                release_id=release.id,
                target_node_id=target.id,
                scope_kind=target.node_kind,
                scope_id=target.source_id,
                kind="review",
                requester_id=actor.id,
                request_key=data.request_key,
                request_digest=request_digest,
                content_digest=workflow_content_digest(subject),
                note=data.note or None,
                due_on=data.due_on,
            )
            self.workflow.repository.create(db, request, [])
            self._bind(db, request, publication.id, data.target_node_id, recipients)
            AuditLogService().record(
                db,
                event_type="requirement_workflow.guest_review_requested",
                actor_user_id=actor.id,
                project_id=project_id,
                resource_type="requirement_workflow",
                metadata={
                    "request_id": str(request.id),
                    "publication_id": str(publication.id),
                },
                commit=False,
            )
            return self.workflow._reads(db, [request])[0], True

    def _selected_recipients(self, db, actor, project_id, publication_id, grant_ids):
        recipients = [
            row
            for row in self.recipients.recipients(db, actor, project_id, publication_id)
            if row["grant_id"] in grant_ids
        ]
        if len(recipients) != len(grant_ids):
            raise BadRequestError("現在有効な共有先を指定してください。")
        return recipients

    @staticmethod
    def _check_locked_users(recipients, related):
        if {row["user_id"] for row in recipients} != set(related):
            raise BadRequestError("現在有効な共有先を指定してください。")

    @staticmethod
    def _bind(db, request, publication_id, target_node_id, recipients):
        # 本人に組織所属を付与せず、照合済みの許可と同時に担当を保存する。
        db.execute(
            insert(Base.metadata.tables[RequirementWorkflowParticipant.__tablename__]),
            [
                {"request_id": request.id, "user_id": row["user_id"]}
                for row in recipients
            ],
        )
        db.execute(
            insert(Base.metadata.tables[RequirementWorkflowGuestBinding.__tablename__]),
            [
                dict(
                    request_id=request.id,
                    user_id=row["user_id"],
                    project_id=request.project_id,
                    publication_id=publication_id,
                    target_node_id=target_node_id,
                    grant_id=row["grant_id"],
                    participation_id=row["participation_id"],
                )
                for row in recipients
            ],
        )

    def _prepare_formal(
        self, db, actor, project_id, release, publication, data, recipients
    ):
        self.workflow.releases._permission(
            db, actor, project_id, "artifact_release:create"
        )
        if publication.public_digest != data.expected_public_digest:
            raise VersionConflictError({"public_digest": publication.public_digest})
        guests = tuple(
            RequirementWorkflowRecipient(id=row["user_id"], name=row["name"])
            for row in recipients
        )
        approval_input = RequirementFormalApprovalInput(
            document_version=release.source_version,
            participant_ids=[*data.participant_ids, *(person.id for person in guests)],
            included_sources=[
                ArtifactSource.model_validate(item) for item in release.source_refs[1:]
            ],
            note=data.note,
            due_on=data.due_on,
            exceptions=data.exceptions,
        )
        prepared = self.formal._prepare(
            db,
            actor,
            project_id,
            release.source_id,
            approval_input,
            guest_recipients=guests,
        )
        if release.content_digest != prepared.snapshot.snapshot.scope_digest:
            raise ConflictError(
                "最新の内容で要件定義書の版を保存し、承認者へ共有してください。"
            )
        prepared.preview.confirmation_digest = digest(
            {
                "approval": prepared.preview.confirmation_digest,
                "publication": str(publication.id),
                "public_digest": publication.public_digest,
                "grants": sorted(str(grant) for grant in data.grant_ids),
            }
        )
        return approval_input, prepared

    @retry_publication_operation
    def formal_preview(self, original, user, project_id, data):
        with self.shared.context.transaction(original, user, project_id) as (db, actor):
            publication, release = self._source(
                db, actor, project_id, data.publication_id
            )
            recipients = self._selected_recipients(
                db, actor, project_id, publication.id, data.grant_ids
            )
            _, prepared = self._prepare_formal(
                db, actor, project_id, release, publication, data, recipients
            )
            return prepared.preview

    @retry_publication_operation
    def create_formal(
        self, original, user, project_id, data: GuestRequirementFormalApprovalCreate
    ):
        related = self._related_users(original, project_id, data)
        with self.shared.context.transaction(
            original,
            user,
            project_id,
            write=True,
            related_user_ids=(*related, *data.participant_ids),
        ) as (db, actor):
            publication, release = self._source(
                db, actor, project_id, data.publication_id
            )
            payload_digest = digest(
                {"guest_formal_approval": data.model_dump(mode="json")}
            )
            previous = self.workflow.repository.by_key(
                db, project_id, actor.id, data.request_key
            )
            if previous is not None:
                if previous.request_digest != payload_digest:
                    raise DuplicateResourceError()
                return self.workflow._reads(db, [previous])[0], False
            recipients = self._selected_recipients(
                db, actor, project_id, publication.id, data.grant_ids
            )
            self._check_locked_users(recipients, related)
            document = self.workflow._document(db, project_id, release.source_id)
            db.refresh(document, with_for_update=True)
            approval_input, prepared = self._prepare_formal(
                db, actor, project_id, release, publication, data, recipients
            )
            self.formal._check_submission(prepared.preview, data.expected_digest)
            request = self.formal._save_request(
                db,
                actor,
                project_id,
                document.id,
                RequirementFormalApprovalCreate(
                    **approval_input.model_dump(),
                    request_key=data.request_key,
                    expected_digest=data.expected_digest,
                    confirmed_content=True,
                ),
                prepared,
                payload_digest,
                release_id=release.id,
                internal_ids=data.participant_ids,
            )
            target = uuid5(publication.id, str(request.target_node_id))
            self._bind(db, request, publication.id, target, recipients)
            return self.workflow._reads(db, [request])[0], True

    def _visibility(self, actor_id):
        bindings = RequirementWorkflowGuestBinding.__table__
        publications = self.shared.repository.shared_query(actor_id).subquery()
        return (
            select(
                bindings.c.request_id,
                bindings.c.publication_id,
                bindings.c.project_id,
                bindings.c.target_node_id,
                publications.c.effective_expiry,
            )
            .join(
                publications,
                and_(
                    publications.c.id == bindings.c.publication_id,
                    publications.c.project_id == bindings.c.project_id,
                    publications.c.grant_id == bindings.c.grant_id,
                    publications.c.participant_id == bindings.c.participation_id,
                ),
            )
            .where(bindings.c.user_id == actor_id)
        )

    @contextmanager
    def _request(
        self, original, user, publication_id, request_id, *, write=False, kind="review"
    ):
        with self.shared.context.transaction(original, user, write=write) as (
            db,
            actor,
        ):
            check = self._visibility(actor.id)
            b = RequirementWorkflowGuestBinding.__table__
            check = check.where(
                b.c.request_id == request_id, b.c.publication_id == publication_id
            )
            binding = db.execute(check).mappings().first()
            if binding is None:
                raise NotFoundError()
            if write:
                db.execute(
                    text("SELECT publication_lock(:project,false)"),
                    {"project": binding["project_id"]},
                )
            publication, revision = self.shared._get(
                db, original, actor, publication_id
            )
            binding = db.execute(check).mappings().first()
            if binding is None:
                raise NotFoundError()
            request = self.workflow.repository.get(
                db, binding["project_id"], request_id, lock=write
            )
            if request is None or request.kind != kind or request.status == "cancelled":
                raise NotFoundError()
            SharedFixedApprovalService.mfa(db, actor)
            yield db, actor, request, binding, publication, revision

    @staticmethod
    def _summary(row, publication_id, title, status, expires_at):
        prefix = "R" if row.kind == "review" else "A"
        return SharedRequirementReviewSummary(
            id=row.id,
            publication_id=publication_id,
            scope_kind=row.scope_kind,
            title=title,
            status=row.status,
            version_label=f"{prefix}-{row.display_number:03d}",
            due_on=row.due_on,
            created_at=row.created_at,
            expires_at=expires_at,
            responded=status != "pending",
        )

    @retry_publication_operation
    def listing(
        self, original, user, publication_id, page, page_size, *, kind="review"
    ):
        with self.shared.context.transaction(original, user) as (db, actor):
            SharedFixedApprovalService.mfa(db, actor)
            visible = self._visibility(actor.id)
            b = RequirementWorkflowGuestBinding.__table__
            if publication_id is not None:
                visible = visible.where(b.c.publication_id == publication_id)
            projects = sorted(
                set(db.scalars(visible.with_only_columns(b.c.project_id)))
            )
            for project in projects:
                db.execute(text("SELECT publication_lock(:id,true)"), {"id": project})
            self.shared.context.verify(db, original, actor, internal=False)
            SharedFixedApprovalService.mfa(db, actor)
            allowed = visible.where(b.c.project_id.in_(projects)).subquery()
            request, participant, node = (
                RequirementWorkflowRequest,
                RequirementWorkflowParticipant,
                ArtifactPublicationNode,
            )
            query = (
                select(
                    request,
                    allowed.c.publication_id,
                    node.payload["title"].astext,
                    participant.status,
                    allowed.c.effective_expiry,
                )
                .join(allowed, request.id == allowed.c.request_id)
                .join(
                    participant,
                    and_(
                        participant.request_id == request.id,
                        participant.user_id == actor.id,
                    ),
                )
                .join(
                    node,
                    and_(
                        node.id == allowed.c.target_node_id,
                        node.publication_id == allowed.c.publication_id,
                    ),
                )
                .where(request.kind == kind, request.status != "cancelled")
            )
            total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = db.execute(
                query.order_by(request.created_at.desc(), request.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            return SharedRequirementReviewList(
                items=[self._summary(*row) for row in rows], total=total
            )

    def _read(self, db, actor, request, binding, revision):
        target = db.get(ArtifactPublicationNode, binding["target_node_id"])
        participant = self.review.repository.participant(db, request.id, actor.id)
        if target is None or participant is None:
            raise NotFoundError()
        total, confirmed, blocked = self.review.repository.counts(db, request, actor.id)
        return SharedRequirementReviewRead(
            **self._summary(
                request,
                binding["publication_id"],
                target.payload["title"],
                participant.status,
                binding["effective_expiry"],
            ).model_dump(),
            note=request.note,
            target_node_id=target.id,
            version=request.version,
            can_respond=request.status == "requested"
            and participant.status == "pending",
            can_discuss=True,
            requirement_count=total,
            confirmed_count=confirmed,
            changes_requested_count=blocked,
            authorization_revision=revision,
        )

    @retry_publication_operation
    def metadata(self, original, user, publication_id, request_id):
        with self._request(original, user, publication_id, request_id) as (
            db,
            actor,
            request,
            binding,
            _,
            revision,
        ):
            return self._read(db, actor, request, binding, revision)

    def _previous_publication_id(self, db, actor, request):
        """依頼時点の開発基準のうち、今も本人へ共有されている公開版を探す。"""
        basis = RequirementFormalApprovalBasis.model_validate(request.approval_basis)
        if basis.previous_baseline_id is None:
            return None
        prior = self.workflow.repository.get(
            db, request.project_id, basis.previous_baseline_id
        )
        if (
            prior is None
            or prior.status != "approved"
            or prior.document_id != request.document_id
        ):
            return None
        p = self.shared.repository.publications
        return db.scalar(
            self.shared.repository.shared_query(actor.id)
            .with_only_columns(p.c.id)
            .where(
                p.c.project_id == request.project_id, p.c.release_id == prior.release_id
            )
            .order_by(p.c.created_at.desc(), p.c.id.desc())
            .limit(1)
        )

    def _formal_read(
        self, db, actor, request, binding, publication, revision, participant
    ):
        return SharedRequirementFormalApprovalRead(
            **self._summary(
                request,
                publication["id"],
                publication["title"],
                participant.status,
                binding["effective_expiry"],
            ).model_dump(),
            note=request.note,
            version=request.version,
            can_decide=request.status == "requested"
            and participant.status == "pending",
            public_digest=publication["public_digest"],
            previous_publication_id=self._previous_publication_id(db, actor, request),
            authorization_revision=revision,
        )

    @retry_publication_operation
    def formal_metadata(self, original, user, publication_id, request_id):
        with self._request(
            original, user, publication_id, request_id, kind="formal_approval"
        ) as (db, actor, request, binding, publication, revision):
            participant = self.review.repository.participant(db, request.id, actor.id)
            if participant is None:
                raise NotFoundError()
            return self._formal_read(
                db, actor, request, binding, publication, revision, participant
            )

    @staticmethod
    def _comparison_content(db, publication):
        """論理IDは照合にだけ使い、本文と階層には公開用ノードを使う。"""
        source = ArtifactReleaseNode
        node = ArtifactPublicationNode
        original_ids = dict(
            db.execute(
                select(source.id, source.source_id).where(
                    source.release_id == publication["release_id"],
                    source.node_kind.in_((*CONTENT_KINDS, *ATTACHMENT_ROOTS)),
                )
            )
            .tuples()
            .all()
        )
        rows = db.execute(
            select(node.id, node.parent_id, node.node_kind, node.payload)
            .where(
                node.publication_id == publication["id"],
                node.node_kind.in_((*CONTENT_KINDS, *ATTACHMENT_ROOTS)),
            )
            .order_by(node.ordinal)
        ).all()
        mappings = publication["source_manifest"]["id_map"]
        identities = {row.id: original_ids[UUID(mappings[str(row.id)])] for row in rows}
        keys = {row.id: f"{row.node_kind}:{identities[row.id]}" for row in rows}
        return (
            [
                ContentNode(
                    key=keys[row.id],
                    kind=row.node_kind,
                    source_id=identities[row.id],
                    parent=keys.get(row.parent_id),
                    payload=row.payload,
                )
                for row in rows
                if row.node_kind in CONTENT_KINDS
            ],
            {key: identity for identity, key in keys.items()},
            {keys[row.id]: row for row in rows if row.node_kind in ATTACHMENT_ROOTS},
        )

    @staticmethod
    def _attachment_changes(before, after):
        changes = []
        for key in dict.fromkeys([*after, *before]):
            old, new = before.get(key), after.get(key)
            if old is None:
                changes.append(
                    SharedRequirementApprovalChange(
                        kind="added", before_id=None, after_id=new.id, fields=[]
                    )
                )
            elif new is None:
                changes.append(
                    SharedRequirementApprovalChange(
                        kind="removed", before_id=old.id, after_id=None, fields=[]
                    )
                )
            else:
                fields = (
                    ("title", "kind", "summary", "content")
                    if new.node_kind == "specification"
                    else ("title", "graph_digest")
                )
                changed = [
                    field
                    for field in fields
                    if old.payload.get(field) != new.payload.get(field)
                ]
                if changed:
                    changes.append(
                        SharedRequirementApprovalChange(
                            kind="changed",
                            before_id=old.id,
                            after_id=new.id,
                            fields=changed,
                        )
                    )
        return changes

    @retry_publication_operation
    def formal_comparison(
        self, original, user, publication_id, request_id, page, page_size
    ):
        """同じ案件で有効な二つの公開許可の範囲に差分を限定する。"""
        with self._request(
            original, user, publication_id, request_id, kind="formal_approval"
        ) as (db, actor, request, binding, publication, revision):
            previous_id = self._previous_publication_id(db, actor, request)
            if previous_id is None:
                raise NotFoundError()
            previous, previous_revision = self.shared._get(
                db, original, actor, previous_id
            )
            before, before_ids, before_attachments = self._comparison_content(
                db, previous
            )
            after, after_ids, after_attachments = self._comparison_content(
                db, publication
            )
            changes = compare_content(before, after)
            attachment_changes = self._attachment_changes(
                before_attachments, after_attachments
            )
            now = db.scalar(select(func.clock_timestamp()))
            if min(binding["effective_expiry"], previous["effective_expiry"]) <= now:
                raise NotFoundError()
            self.shared.context.verify(db, original, actor, internal=False)
            SharedFixedApprovalService.mfa(db, actor)
            offset = (page - 1) * page_size
            return SharedRequirementApprovalComparison(
                previous_publication_id=previous_id,
                previous_authorization_revision=previous_revision,
                authorization_revision=revision,
                items=(
                    [
                        SharedRequirementApprovalChange(
                            kind=item.change,
                            before_id=before_ids.get(f"{item.kind}:{item.id}"),
                            after_id=after_ids.get(f"{item.kind}:{item.id}"),
                            fields=item.fields,
                        )
                        for item in changes
                    ]
                    + attachment_changes
                )[offset : offset + page_size],
                total=len(changes) + len(attachment_changes),
            )

    @retry_publication_operation
    def decide_formal(self, original, user, publication_id, request_id, data):
        with self._request(
            original,
            user,
            publication_id,
            request_id,
            write=True,
            kind="formal_approval",
        ) as (db, actor, request, binding, publication, revision):
            participant = self.review.repository.participant(db, request.id, actor.id)
            if participant is None:
                raise NotFoundError()
            if data.expected_public_digest != publication["public_digest"]:
                raise VersionConflictError({"version": request.version})
            self.formal._apply_decision(db, actor, request, participant, data)
            return self._formal_read(
                db, actor, request, binding, publication, revision, participant
            )

    @staticmethod
    def _internal_node(publication, public_node):
        try:
            return UUID(publication["source_manifest"]["id_map"][str(public_node)])
        except (KeyError, ValueError, TypeError):
            raise NotFoundError() from None

    @retry_publication_operation
    def nodes(self, original, user, publication_id, request_id, cursor, page_size):
        with self._request(original, user, publication_id, request_id) as (
            db,
            actor,
            request,
            binding,
            publication,
            revision,
        ):
            t = ArtifactPublicationNode.__table__
            target = binding["target_node_id"]
            roots = select(t.c.id).where(
                t.c.publication_id == publication_id,
                or_(
                    t.c.id == target,
                    and_(
                        t.c.parent_id.is_(None),
                        t.c.node_kind.in_(ATTACHMENT_ROOTS),
                    ),
                ),
            )
            tree = roots.cte("guest_review_scope", recursive=True)
            tree = tree.union_all(
                select(t.c.id)
                .join(tree, t.c.parent_id == tree.c.id)
                .where(t.c.publication_id == publication_id)
            )
            purpose = f"shared-review:{request.id}"
            offset = self.shared._cursor(cursor, purpose, revision)
            models = db.scalars(
                select(ArtifactPublicationNode)
                .join(tree, t.c.id == tree.c.id)
                .where(t.c.publication_id == publication_id, t.c.ordinal >= offset)
                .order_by(t.c.ordinal)
                .limit(page_size + 1)
            ).all()
            result = SharedRequirementReviewNodes(
                items=[], states=[], next_cursor=None, authorization_revision=revision
            )
            for index, model in enumerate(models[:page_size]):
                item = PublicationNodeRead.model_validate(model)
                if item.id == target:
                    item = item.model_copy(update={"parent_id": None})
                    item.content_digest = digest(
                        item.model_dump(mode="json", exclude={"content_digest"})
                    )
                proposed = result.model_copy(
                    update={
                        "items": [*result.items, item],
                        "next_cursor": encode_cursor(
                            0, purpose, revision, item.ordinal + 1
                        )
                        if index + 1 < len(models)
                        else None,
                    }
                )
                # 確認状態を加えた後も上限を守る。
                if (
                    len(proposed.model_dump_json().encode())
                    > MAX_PAGE_BYTES - page_size * 128
                ):
                    if not result.items:
                        raise ConflictError()
                    break
                result = proposed
            ids = [
                self._internal_node(publication, item.id)
                for item in result.items
                if item.node_kind == "requirement"
            ]
            result.states = [
                item.model_copy(
                    update={"node_id": uuid5(publication_id, str(item.node_id))}
                )
                for item in self.review._progress(db, actor, request, ids).items
            ]
            return result

    @retry_publication_operation
    def confirm(self, original, user, publication_id, request_id, public_node_id, data):
        with self._request(original, user, publication_id, request_id, write=True) as (
            db,
            actor,
            request,
            binding,
            publication,
            revision,
        ):
            node = self._internal_node(publication, public_node_id)
            self.review._set_confirmation(db, actor, request, node, data)
            return self._read(db, actor, request, binding, revision)

    @retry_publication_operation
    def submit(self, original, user, publication_id, request_id, data):
        with self._request(original, user, publication_id, request_id, write=True) as (
            db,
            actor,
            request,
            binding,
            _,
            revision,
        ):
            tenant_id = db.scalar(
                select(Project.tenant_id).where(Project.id == request.project_id)
            )
            self.review._submit(
                db, actor, request, data, request.project_id, tenant_id=tenant_id
            )
            return self._read(db, actor, request, binding, revision)

    def _own_thread(self, db, actor, request, identity):
        thread = self.review.repository.thread(db, request.id, identity)
        if thread is None or thread.author_id != actor.id:
            raise NotFoundError()
        return thread

    def _public_thread(self, db, actor, request, publication_id, thread):
        actions = self.review._available_actions(db, actor, request, thread)
        return SharedRequirementReviewThread.model_validate(
            {
                "id": thread.id,
                "node_id": uuid5(publication_id, str(thread.node_id)),
                "kind": thread.kind,
                "body": thread.body,
                "status": thread.status,
                "version": thread.version,
                "created_at": thread.created_at,
                "available_actions": [
                    action
                    for action in actions
                    if action in {"reply", "reopen", "resolve"}
                ],
            }
        )

    @retry_publication_operation
    def threads(
        self, original, user, publication_id, request_id, public_node, page, page_size
    ):
        with self._request(original, user, publication_id, request_id) as (
            db,
            actor,
            request,
            _,
            publication,
            _,
        ):
            node = self._internal_node(publication, public_node)
            if self.review.repository.node(db, request, node) is None:
                raise NotFoundError()
            rows, total = self.review.repository.threads(
                db,
                request.id,
                page,
                page_size,
                active_only=False,
                node_id=node,
                author_id=actor.id,
            )
            return SharedRequirementReviewThreads(
                items=[
                    self._public_thread(db, actor, request, publication_id, row)
                    for row in rows
                ],
                total=total,
            )

    @retry_publication_operation
    def add_thread(self, original, user, publication_id, request_id, data):
        with self._request(original, user, publication_id, request_id, write=True) as (
            db,
            actor,
            request,
            _,
            publication,
            _,
        ):
            node = self._internal_node(publication, data.node_id)
            thread = self.review._store_thread(
                db, actor, request, data.model_copy(update={"node_id": node})
            )
            return self._public_thread(db, actor, request, publication_id, thread)

    @retry_publication_operation
    def act_on_thread(self, original, user, publication_id, request_id, identity, data):
        with self._request(original, user, publication_id, request_id, write=True) as (
            db,
            actor,
            request,
            _,
            _,
            _,
        ):
            thread = self._own_thread(db, actor, request, identity)
            if data.action not in {"reply", "resolve", "reopen"}:
                raise ForbiddenError()
            updated = self.review._act_on_thread(db, actor, request, thread, data)
            return self._public_thread(db, actor, request, publication_id, updated)

    @retry_publication_operation
    def replies(
        self, original, user, publication_id, request_id, identity, page, page_size
    ):
        with self._request(original, user, publication_id, request_id) as (
            db,
            actor,
            request,
            _,
            _,
            _,
        ):
            thread = self._own_thread(db, actor, request, identity)
            event = RequirementReviewThreadEvent
            query = select(event.body).where(
                event.thread_id == thread.id,
                event.body.is_not(None),
                event.action.in_(("reply", "respond")),
            )
            total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
            items = db.scalars(
                query.order_by(event.id).offset((page - 1) * page_size).limit(page_size)
            )
            return SharedRequirementReviewReplies(
                items=[body for body in items if body is not None], total=total
            )
