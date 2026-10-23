"""固定対象、指定本人、文書の例外と業務履歴を同じ境界で扱う。"""

from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4, uuid5

from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core import error_messages
from app.core.exceptions import (
    AppError,
    BadRequestError,
    DuplicateResourceError,
    FixedApprovalScopeConflictError,
    FixedApprovalTooLargeError,
    ForbiddenError,
    NotFoundError,
    ObjectiveProjectionUpgradeError,
    VersionConflictError,
)
from app.models.artifact_preservation import ArtifactPreservation
from app.models.fixed_approval import (
    FixedApprovalEvent,
    FixedApprovalRequest,
    FixedApprovalRequirementBasis,
)
from app.models.requirement import Requirement, RequirementDocument
from app.models.specification import Specification
from app.models.test_design import TestDesign
from app.models.user import User
from app.repositories.fixed_approval import FixedApprovalRepository
from app.schemas.artifact_release import ArtifactPreviewRequest, ProjectionVersion
from app.schemas.fixed_approval import (
    ApprovalBasisPage,
    ApprovalBasisRead,
    ApprovalCandidate,
    ApprovalCandidates,
    ApprovalDecisionPreview,
    FixedApprovalEventRead,
    FixedApprovalPreview,
    FixedApprovalRead,
    GuestApprovalContext,
)
from app.services.artifact_release import SOURCE_PERMISSIONS, ArtifactReleaseService
from app.services.artifact_release_snapshot import (
    canonical_bytes,
    digest,
)
from app.services.audit_log import AuditLogService
from app.services.fixed_approval_cursor import decode_cursor, encode_cursor
from app.services.fixed_approval_notification import emit_fixed_approval
from app.services.fixed_approval_scope import MAX_REVIEW_INPUT, requirement_digest


class FixedApprovalService:
    """既存role割当を広げず、内部の固定版承認だけを提供する。"""

    def __init__(self):
        """既存の固定版・認可と、commitしないRepositoryを使う。"""
        self.releases = ArtifactReleaseService()
        self.repository = FixedApprovalRepository()

    @contextmanager
    def access(self, original, user, project_id, *, write=False):
        """現在のsession・所属を新しいRR transactionで確認する。"""
        with self.releases._transaction(original, user, project_id, None) as (
            db,
            actor,
        ):
            self.releases._permission(db, actor, project_id, "requirement:read")
            if write:
                self.releases._permission(
                    db, actor, project_id, "artifact_release:create"
                )
                self.releases._permission(db, actor, project_id, "requirement:review")
            yield db, actor

    def allowed(self, db, actor, project_id):
        """固定版の存在・件数も現在読める種類に限定する。"""
        auth = self.releases.authorization
        if not auth.has_project_permission(
            db,
            user=actor,
            project_id=project_id,
            permission_code="artifact_release:read",
        ):
            return [], False
        kinds = [
            kind
            for kind, prefix in SOURCE_PERMISSIONS.items()
            if auth.has_project_permission(
                db,
                user=actor,
                project_id=project_id,
                permission_code=f"{prefix}:read",
            )
        ]
        document_read = auth.has_project_permission(
            db, user=actor, project_id=project_id, permission_code="document:read"
        )
        return kinds, document_read

    def release(
        self, db, actor, project_id, release_id, preservation_id=None, *, write=False
    ):
        """本文の全sourceと、選択した完成済み添付の所属を確認する。"""
        self.releases._permission(
            db, actor, project_id, f"artifact_release:{'create' if write else 'read'}"
        )
        row = self.releases._get(db, actor, project_id, release_id)
        preservation = None
        if preservation_id is not None:
            self.releases._permission(db, actor, project_id, "document:read")
            preservation = db.scalar(
                select(ArtifactPreservation).where(
                    ArtifactPreservation.id == preservation_id,
                    ArtifactPreservation.release_id == release_id,
                )
            )
            if preservation is None:
                raise NotFoundError()
            if preservation.status != "ready":
                raise FixedApprovalScopeConflictError()
        return row, preservation

    @staticmethod
    def check_input(data):
        """入力を切り捨てず容量超過を明示する。"""
        if len(canonical_bytes(data.model_dump(mode="json"))) > MAX_REVIEW_INPUT:
            raise FixedApprovalTooLargeError()

    @staticmethod
    def retry(operation):
        """同時判断と冪等作成競合を新しいtransactionで再確認する。"""
        for attempt in range(3):
            try:
                return operation()
            except DBAPIError as exc:
                code = getattr(exc.orig, "pgcode", None)
                constraint = getattr(
                    getattr(exc.orig, "diag", None), "constraint_name", None
                )
                if code == "23505" and constraint == "uq_fixed_approval_pending":
                    raise DuplicateResourceError() from None
                if code not in {"40001", "40P01"} and not (
                    code == "23505"
                    and constraint
                    in {
                        "uq_fixed_approval_operation",
                        "uq_legacy_approval_operation",
                        "uq_legacy_approval_snapshot",
                        "uq_legacy_approval_switch",
                    }
                ):
                    raise
                if attempt == 2:
                    raise FixedApprovalScopeConflictError() from None
        raise AssertionError("unreachable")

    def candidate_codes(self, release, preservation_id):
        """候補には判断に必要な全readと既存approveを要求する。"""
        codes = {"artifact_release:read", "requirement:read", "requirement:approve"}
        codes.update(
            f"{SOURCE_PERMISSIONS[kind]}:read" for kind in release.source_kinds
        )
        if preservation_id:
            codes.add("document:read")
        return sorted(codes)

    def candidates(
        self,
        original,
        user,
        project_id,
        release_id,
        preservation_id,
        q,
        page,
        page_size,
    ):
        """適格な別本人を、不要な個人情報を含めず返す。"""
        with self.access(original, user, project_id, write=True) as (db, actor):
            release, _ = self.release(
                db, actor, project_id, release_id, preservation_id, write=True
            )
            query = self.repository.candidates(
                db,
                project_id,
                actor.id,
                self.candidate_codes(release, preservation_id),
                q,
            )
            total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = db.scalars(
                query.order_by(User.id).offset((page - 1) * page_size).limit(page_size)
            )
            return ApprovalCandidates(
                items=[ApprovalCandidate(id=row.id, name=row.name) for row in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

    def basis_rows(self, db, actor, project_id, release, nodes, target):
        """同じ要件ID・版・本文への合意だけを文書の根拠にする。"""
        if target.node_kind != "requirement_document":
            return []
        members = {target.id}
        for node in nodes:
            if node.parent_id in members:
                members.add(node.id)
        reqs = [
            node
            for node in nodes
            if node.id in members and node.node_kind == "requirement"
        ]
        kinds, document_read = self.allowed(db, actor, project_id)
        query = self.repository.visible(
            project_id, kinds, document_read=document_read
        ).where(
            FixedApprovalRequest.target_kind == "requirement",
            FixedApprovalRequest.source_id.in_([node.source_id for node in reqs]),
        )
        previous = list(
            db.scalars(
                query.order_by(
                    FixedApprovalRequest.created_at.desc(),
                    FixedApprovalRequest.id.desc(),
                )
            )
        )
        by_source = {}
        for approval in previous:
            by_source.setdefault(approval.source_id, []).append(approval)
        legacy_ids = {
            row.target_id
            for row in db.scalars(self.repository.legacy_visible(project_id))
            if row.target_type == "requirement_item"
        }
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
            histories = by_source.get(node.source_id, [])
            same = [
                row
                for row in histories
                if row.scope_manifest.get("requirement_digest") == subject
            ]
            approved = [row for row in same if row.status == "approved"]
            rejected = any(row.status == "rejected" for row in same)
            state, agreed, event = "unagreed", None, None
            if approved and rejected:
                state = "conflicting"
            elif approved:
                agreed = approved[0].id
                event = self.repository.events(db, agreed)[-1].id
                state = "agreed"
            elif rejected:
                state = "rejected"
            elif any(row.status == "requested" for row in same):
                state = "pending"
            elif histories:
                state = "changed"
            elif node.source_id in legacy_ids:
                state = "legacy_unverified"
            result.append(
                ApprovalBasisRead(
                    node_id=node.id,
                    source_id=node.source_id,
                    source_version=node.source_version,
                    title=node.payload["title"],
                    subject_digest=subject,
                    state=state,
                    agreed_approval_id=agreed,
                    agreed_event_id=event,
                )
            )
        return result

    def scope(self, db, actor, project_id, data):
        """指定された固定対象と全例外の確認結果を作る。"""
        self.check_input(data)
        release, preservation = self.release(
            db, actor, project_id, data.release_id, data.preservation_id, write=True
        )
        nodes = self.repository.nodes(db, release.id)
        target = next((node for node in nodes if node.id == data.target_node_id), None)
        if target is None or target.node_kind not in {
            "requirement",
            "requirement_document",
        }:
            raise NotFoundError()
        if release.source_kind not in {"requirement", "requirement_document"} or any(
            ref["kind"] not in {"specification", "test_design"}
            for ref in release.source_refs[1:]
        ):
            raise FixedApprovalScopeConflictError()
        candidate = db.scalar(
            self.repository.candidates(
                db,
                project_id,
                actor.id,
                self.candidate_codes(release, data.preservation_id),
            ).where(
                User.id == data.approver_id,
            )
        )
        if candidate is None:
            raise BadRequestError(error_messages.FIXED_APPROVAL_CANDIDATE_INVALID)
        bases = self.basis_rows(db, actor, project_id, release, nodes, target)
        exceptions = {item.node_id: item.reason.strip() for item in data.exceptions}
        missing = {item.node_id for item in bases if item.state != "agreed"}
        if set(exceptions) != missing:
            raise FixedApprovalScopeConflictError()
        for item in bases:
            item.reason = exceptions.get(item.node_id)
        origin = None
        if data.legacy_import_id is not None:
            origin = self.repository.imported(db, project_id, data.legacy_import_id)
            expected_kind = (
                {
                    "document": "requirement_document",
                    "requirement_item": "requirement",
                }.get(origin.source["target_type"])
                if origin
                else None
            )
            if (
                origin is None
                or origin.mode != "archive"
                or origin.source["status"] not in {"approved", "rejected"}
                or expected_kind != target.node_kind
                or origin.source["target_id"] != target.source_id
            ):
                raise FixedApprovalScopeConflictError()
        manifest = {
            "policy": "fixed-approval-v1",
            "release_id": str(release.id),
            "body_digest": release.content_digest,
            "target_node_id": str(target.id),
            "target_kind": target.node_kind,
            "source_id": target.source_id,
            "source_version": target.source_version,
            "title": target.payload["title"],
            "requirement_digest": requirement_digest(
                target,
                [
                    node
                    for node in nodes
                    if node.parent_id == target.id
                    and node.node_kind == "requirement_detail"
                ],
            )
            if target.node_kind == "requirement"
            else None,
            "supporting_sources": release.source_refs[1:],
            "preservation_id": str(preservation.id) if preservation else None,
            "preservation_digest": preservation.manifest_digest
            if preservation
            else None,
            "files": [
                {**item, "file_id": str(uuid5(preservation.id, str(index)))}
                for index, item in enumerate(preservation.manifest["files"])
            ]
            if preservation
            else [],
            "basis_count": len(bases),
            "basis_digest": digest([item.model_dump(mode="json") for item in bases]),
            "exception_count": len(exceptions),
            "legacy_origin": {
                "import_id": str(origin.id),
                "source_digest": origin.source_digest,
                "legacy_approval_id": origin.legacy_approval_id,
            }
            if origin
            else None,
        }
        scope_digest = digest(manifest)
        values = data.model_dump(
            mode="json", exclude={"request_id", "expected_preview_digest"}
        )
        preview = FixedApprovalPreview(
            title=manifest["title"],
            target_kind=target.node_kind,
            source_id=target.source_id,
            scope_manifest=manifest,
            scope_digest=scope_digest,
            preview_digest=digest(
                {
                    "scope": scope_digest,
                    "input": values,
                    "actor": actor.id,
                    "project": project_id,
                }
            ),
            basis_counts=dict(Counter(item.state for item in bases)),
        )
        return preview, bases

    def preview(self, original, user, project_id, data):
        """保存を伴わない申請内容の確認。"""
        with self.access(original, user, project_id, write=True) as (db, actor):
            return self.scope(db, actor, project_id, data)[0]

    def audit(self, db, actor, project_id, identity, action):
        """操作監査へ本文・理由・署名URLを載せない。"""
        AuditLogService().record(
            db,
            event_type=f"fixed_approval.{action}",
            actor_user_id=actor.id,
            project_id=project_id,
            resource_type="fixed_approval",
            metadata={"approval_id": str(identity)},
            commit=False,
        )

    def save(
        self,
        db,
        actor,
        project_id,
        data,
        preview,
        bases,
        *,
        request_id,
        request_digest,
        import_id=None,
        guest_binding=None,
    ):
        """依頼・根拠・イベント・封印を同じtransactionへ保存する。"""
        manifest = preview.scope_manifest
        row = FixedApprovalRequest(
            id=uuid4(),
            project_id=project_id,
            release_id=data.release_id,
            target_node_id=data.target_node_id,
            target_kind=preview.target_kind,
            source_id=preview.source_id,
            preservation_id=data.preservation_id,
            scope_manifest=manifest,
            scope_digest=preview.scope_digest,
            policy_version="fixed-approval-guest-v1"
            if guest_binding
            else "fixed-approval-v1",
            requester_id=actor.id,
            approver_id=data.approver_id,
            request_note=data.request_note,
            status="requested",
            version=1,
            sealed=False,
            request_id=request_id,
            request_digest=request_digest,
            legacy_import_id=import_id,
        )
        if guest_binding:
            # 外部Identityを組織内Identityとして汎用ORM検証へ渡さない。
            # 呼出元の本人別許可検証と新DBガードを通した専用insert。
            values = {
                column.name: getattr(row, column.name)
                for column in row.__table__.columns
                if getattr(row, column.name) is not None
            }
            from app.repositories.guest_fixed_approval import (
                GuestFixedApprovalRepository,
            )

            db.execute(GuestFixedApprovalRepository.requests.insert().values(**values))
            saved = db.get(FixedApprovalRequest, row.id)
            assert saved is not None
            row = saved
        else:
            db.add(row)
            db.flush()
        if guest_binding:
            from app.models.guest_fixed_approval import FixedApprovalGuestBinding

            db.add(FixedApprovalGuestBinding(approval_id=row.id, **guest_binding))
            db.flush()
        db.add(
            FixedApprovalEvent(
                id=uuid4(),
                approval_id=row.id,
                seq=1,
                operation_id=request_id,
                operation_digest=request_digest,
                actor_id=actor.id,
                action="requested",
                reason=data.request_note,
                scope_digest=row.scope_digest,
                observation={},
            )
        )
        db.add_all(
            [
                FixedApprovalRequirementBasis(
                    id=uuid4(),
                    approval_id=row.id,
                    release_id=row.release_id,
                    target_node_id=item.node_id,
                    source_id=item.source_id,
                    subject_digest=item.subject_digest,
                    basis_kind="agreed" if item.state == "agreed" else "exception",
                    state_at_request=item.state,
                    agreed_approval_id=item.agreed_approval_id,
                    agreed_event_id=item.agreed_event_id,
                    reason=item.reason,
                )
                for item in bases
            ]
        )
        db.flush()
        row.sealed = True
        db.flush()
        self.audit(db, actor, project_id, row.id, "requested")
        emit_fixed_approval(db, row.id, "requested")
        return row

    def create(self, original, user, project_id, data):
        """再確認した固定内容だけを、本人の申請として冪等保存する。"""
        self.check_input(data)

        def operation():
            with self.access(original, user, project_id, write=True) as (db, actor):
                previous = self.repository.by_operation(
                    db, project_id, actor.id, data.request_id
                )
                request_hash = digest(data.model_dump(mode="json"))
                if previous is not None:
                    self.release(
                        db,
                        actor,
                        project_id,
                        previous.release_id,
                        previous.preservation_id,
                        write=True,
                    )
                    if previous.request_digest != request_hash:
                        raise DuplicateResourceError()
                    return self.read(db, actor, project_id, previous), False
                preview, bases = self.scope(db, actor, project_id, data)
                if preview.preview_digest != data.expected_preview_digest:
                    raise FixedApprovalScopeConflictError()
                row = self.save(
                    db,
                    actor,
                    project_id,
                    data,
                    preview,
                    bases,
                    request_id=data.request_id,
                    request_digest=request_hash,
                    import_id=data.legacy_import_id,
                )
                return self.read(db, actor, project_id, row), True

        return self.retry(operation)

    def read(self, db, actor, project_id, row):
        """保存済みの判断と、現在可能な操作を分けて提示する。"""
        events = [
            FixedApprovalEventRead.model_validate(event)
            for event in self.repository.events(db, row.id)
        ]
        names = {
            user.id: user.name
            for user in db.scalars(
                select(User).where(
                    User.id.in_(
                        {
                            row.requester_id,
                            row.approver_id,
                            *(event.actor_id for event in events),
                        }
                    ),
                    User.deleted_at.is_(None),
                )
            )
        }
        events = [
            event.model_copy(update={"actor_name": names.get(event.actor_id)})
            for event in events
        ]
        from app.repositories.guest_fixed_approval import GuestFixedApprovalRepository

        binding = GuestFixedApprovalRepository().binding(db, row.id, project_id)
        guest_context = None
        if binding:
            accessible = bool(
                db.scalar(
                    text("SELECT guest_approval_has_access(:target,:actor)"),
                    {"target": row.id, "actor": row.approver_id},
                )
            )
            guest_context = GuestApprovalContext(
                publication_id=binding["publication_id"],
                target_node_id=binding["public_target_node_id"],
                can_access=accessible,
                blocked_reason=None
                if accessible
                else (
                    "公開状態・参加資格・公開許可・期限を確認し、"
                    "取消後に再依頼してください"
                ),
            )
        auth = self.releases.authorization
        return FixedApprovalRead.model_validate(row).model_copy(
            update={
                "events": events,
                "requester_name": names.get(row.requester_id),
                "approver_name": names.get(row.approver_id),
                "audience": "guest" if binding else "internal",
                "guest_context": guest_context,
                "can_decide": row.status == "requested"
                and not binding
                and actor.id == row.approver_id
                and auth.has_project_permission(
                    db,
                    user=actor,
                    project_id=project_id,
                    permission_code="requirement:approve",
                ),
                "can_cancel": row.status == "requested"
                and all(
                    auth.has_project_permission(
                        db,
                        user=actor,
                        project_id=project_id,
                        permission_code=code,
                    )
                    for code in ("artifact_release:create", "requirement:review")
                ),
            }
        )

    def get_row(self, db, actor, project_id, approval_id, *, lock=False, write=False):
        """同Projectの依頼と本文・添付への現在権限を確認する。"""
        row = self.repository.get(db, project_id, approval_id, lock=lock)
        if row is None:
            raise NotFoundError()
        self.release(
            db, actor, project_id, row.release_id, row.preservation_id, write=write
        )
        return row

    def get(self, original, user, project_id, approval_id):
        """元要件が削除されていても固定した依頼を読む。"""
        with self.access(original, user, project_id) as (db, actor):
            return self.read(
                db, actor, project_id, self.get_row(db, actor, project_id, approval_id)
            )

    def basis(
        self,
        original,
        user,
        project_id,
        release_id,
        target_node_id,
        approval_id,
        cursor,
        page_size,
    ):
        """申請前の根拠候補、または申請後に固定した根拠をページ取得する。"""
        with self.access(original, user, project_id) as (db, actor):
            if approval_id is not None:
                row = self.get_row(db, actor, project_id, approval_id)
                nodes = {
                    node.id: node for node in self.repository.nodes(db, row.release_id)
                }
                bases = {
                    item.target_node_id: item
                    for item in self.repository.bases(db, row.id)
                }
                items = [
                    ApprovalBasisRead(
                        node_id=node.id,
                        source_id=node.source_id,
                        source_version=node.source_version,
                        title=node.payload["title"],
                        subject_digest=bases[node.id].subject_digest,
                        state=bases[node.id].state_at_request,
                        agreed_approval_id=bases[node.id].agreed_approval_id,
                        agreed_event_id=bases[node.id].agreed_event_id,
                        reason=bases[node.id].reason,
                    )
                    for node in nodes.values()
                    if node.id in bases
                ]
                identity = str(row.id)
            else:
                if release_id is None or target_node_id is None:
                    raise BadRequestError()
                release, _ = self.release(db, actor, project_id, release_id)
                nodes = self.repository.nodes(db, release.id)
                target = next(
                    (node for node in nodes if node.id == target_node_id), None
                )
                if target is None:
                    raise NotFoundError()
                items = self.basis_rows(db, actor, project_id, release, nodes, target)
                identity = f"{release_id}:{target_node_id}"
            scope_hash = digest([item.model_dump(mode="json") for item in items])
            offset = decode_cursor(cursor, project_id, identity, scope_hash)
            page = items[offset : offset + page_size]
            return ApprovalBasisPage(
                items=page,
                total=len(items),
                counts=dict(Counter(item.state for item in items)),
                scope_digest=scope_hash,
                next_cursor=encode_cursor(
                    project_id, identity, scope_hash, offset + len(page)
                )
                if offset + len(page) < len(items)
                else None,
            )

    def observation(self, db, actor, project_id, row):
        """root版だけでなく本文全体を比較し、添付実体の比較とは区別する。"""
        release, _ = self.release(
            db, actor, project_id, row.release_id, row.preservation_id
        )
        result = {
            "body_comparison": "unavailable",
            "current_body_digest": None,
            "fixed_source_version": row.scope_manifest["source_version"],
            "current_source_version": None,
            "attachments_comparison": "fixed_only",
        }
        refs = []
        try:
            for ref in release.source_refs:
                model = {
                    "requirement": Requirement,
                    "requirement_document": RequirementDocument,
                    "specification": Specification,
                    "test_design": TestDesign,
                }[ref["kind"]]
                query = select(model).where(
                    model.id == ref["id"], model.deleted_at.is_(None)
                )
                if model is Requirement:
                    query = query.join(RequirementDocument).where(
                        RequirementDocument.project_id == project_id,
                        RequirementDocument.deleted_at.is_(None),
                    )
                else:
                    query = query.where(model.project_id == project_id)
                current = db.scalar(query)
                if current is None:
                    return result
                refs.append({**ref, "version": current.version})
                if ref["kind"] == row.target_kind and ref["id"] == row.source_id:
                    result["current_source_version"] = current.version
            request = ArtifactPreviewRequest(
                source=refs[0],
                included_sources=refs[1:],
                projection_version=cast(ProjectionVersion, release.projection_version),
            )
            snapshot = self.releases.builder.build(
                db, project_id, request, datetime.now(UTC)
            )
            result["current_body_digest"] = snapshot.scope_digest
            result["body_comparison"] = (
                "equal"
                if snapshot.scope_digest == release.content_digest
                else "changed"
            )
            if (
                row.target_kind == "requirement"
                and result["current_source_version"] is None
            ):
                current_req = db.scalar(
                    select(Requirement)
                    .join(RequirementDocument)
                    .where(
                        Requirement.id == row.source_id,
                        Requirement.deleted_at.is_(None),
                        RequirementDocument.project_id == project_id,
                        RequirementDocument.deleted_at.is_(None),
                    )
                )
                result["current_source_version"] = (
                    current_req.version if current_req else None
                )
        except ObjectiveProjectionUpgradeError:
            result["body_comparison"] = "changed"
        except (AppError, ValidationError):
            pass
        return result

    def review(self, db, actor, project_id, row):
        """指定本人が確認する対象と、内容変化を束縛するdigest。"""
        if row.policy_version == "fixed-approval-guest-v1":
            raise ForbiddenError()
        self.releases._permission(db, actor, project_id, "requirement:approve")
        if actor.id != row.approver_id or actor.id == row.requester_id:
            raise ForbiddenError()
        if row.status != "requested":
            raise VersionConflictError({"version": row.version, "status": row.status})
        observed = self.observation(db, actor, project_id, row)
        return ApprovalDecisionPreview(
            approval_id=row.id,
            version=row.version,
            review_digest=digest(
                {
                    "id": str(row.id),
                    "version": row.version,
                    "scope": row.scope_digest,
                    "observation": observed,
                }
            ),
            observation=observed,
            exception_count=row.scope_manifest["exception_count"],
        )

    def decision_preview(self, original, user, project_id, approval_id):
        """判断前に現在の権限と内容変化を再確認する。"""
        with self.access(original, user, project_id) as (db, actor):
            return self.review(
                db, actor, project_id, self.get_row(db, actor, project_id, approval_id)
            )

    @staticmethod
    def previous_event(events, data):
        """同一operationの入力差し替えを拒否する。"""
        previous = next(
            (item for item in events if item.operation_id == data.operation_id), None
        )
        if previous and previous.operation_digest != digest(
            data.model_dump(mode="json")
        ):
            raise DuplicateResourceError()
        return previous

    def decide(self, original, user, project_id, approval_id, data):
        """指定本人の判断を、状態・業務履歴・監査と一括確定する。"""
        self.check_input(data)

        def operation():
            with self.access(original, user, project_id) as (db, actor):
                row = self.get_row(db, actor, project_id, approval_id, lock=True)
                if row.policy_version == "fixed-approval-guest-v1":
                    raise ForbiddenError()
                self.releases._permission(db, actor, project_id, "requirement:approve")
                if actor.id != row.approver_id or actor.id == row.requester_id:
                    raise ForbiddenError()
                if self.previous_event(self.repository.events(db, row.id), data):
                    return self.read(db, actor, project_id, row)
                review = self.review(db, actor, project_id, row)
                if row.version != data.version:
                    raise VersionConflictError(
                        {"version": row.version, "status": row.status}
                    )
                if (
                    review.review_digest != data.expected_review_digest
                    or not data.confirm_fixed_scope
                    or (
                        review.observation["body_comparison"] != "equal"
                        and not data.confirm_changed_or_unknown
                    )
                    or (review.exception_count and not data.confirm_exceptions)
                ):
                    raise FixedApprovalScopeConflictError()
                reason = (data.comment or "").strip() or None
                if data.decision == "reject" and reason is None:
                    raise BadRequestError(error_messages.FIXED_APPROVAL_REASON_REQUIRED)
                row.status = (
                    "rejected"
                    if data.decision == "reject"
                    else (
                        "approved_with_exceptions"
                        if review.exception_count
                        else "approved"
                    )
                )
                row.version = 2
                row.finalized_at = datetime.now(UTC)
                db.flush()
                db.add(
                    FixedApprovalEvent(
                        id=uuid4(),
                        approval_id=row.id,
                        seq=2,
                        operation_id=data.operation_id,
                        operation_digest=digest(data.model_dump(mode="json")),
                        actor_id=actor.id,
                        action=row.status,
                        reason=reason,
                        scope_digest=row.scope_digest,
                        observation=review.observation,
                    )
                )
                self.audit(db, actor, project_id, row.id, row.status)
                db.flush()
                emit_fixed_approval(db, row.id, row.status)
                return self.read(db, actor, project_id, row)

        return self.retry(operation)

    def cancel(self, original, user, project_id, approval_id, data):
        """現在の申請権限を持つ管理担当者が、理由付きで取消す。"""

        from app.repositories.guest_fixed_approval import GuestFixedApprovalRepository
        from app.services.guest_fixed_approval import GuestFixedApprovalService

        if GuestFixedApprovalRepository().binding(original, approval_id, project_id):
            return GuestFixedApprovalService().cancel(
                original, user, project_id, approval_id, data
            )

        def operation():
            with self.access(original, user, project_id, write=True) as (db, actor):
                row = self.get_row(
                    db, actor, project_id, approval_id, lock=True, write=True
                )
                if self.previous_event(self.repository.events(db, row.id), data):
                    return self.read(db, actor, project_id, row)
                if row.status != "requested" or row.version != data.version:
                    raise VersionConflictError(
                        {"version": row.version, "status": row.status}
                    )
                if not data.reason.strip():
                    raise BadRequestError(error_messages.FIXED_APPROVAL_REASON_REQUIRED)
                row.status, row.version, row.finalized_at = (
                    "cancelled",
                    2,
                    datetime.now(UTC),
                )
                db.flush()
                db.add(
                    FixedApprovalEvent(
                        id=uuid4(),
                        approval_id=row.id,
                        seq=2,
                        operation_id=data.operation_id,
                        operation_digest=digest(data.model_dump(mode="json")),
                        actor_id=actor.id,
                        action="cancelled",
                        reason=data.reason.strip(),
                        scope_digest=row.scope_digest,
                        observation={},
                    )
                )
                self.audit(db, actor, project_id, row.id, "cancelled")
                db.flush()
                emit_fixed_approval(db, row.id, "cancelled")
                return self.read(db, actor, project_id, row)

        return self.retry(operation)
