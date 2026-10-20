"""既存の固定承認へ、現在許可済みの外部本人への依頼を追加する。"""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import func, select

from app.core.config import settings
from app.core.exceptions import (
    DuplicateResourceError,
    FixedApprovalScopeConflictError,
    FixedApprovalTooLargeError,
    ForbiddenError,
    NotFoundError,
    VersionConflictError,
)
from app.models.fixed_approval import FixedApprovalEvent
from app.models.user import User
from app.repositories.artifact_publication_access import PublicationAccessRepository
from app.repositories.guest_fixed_approval import GuestFixedApprovalRepository
from app.schemas.fixed_approval import FixedApprovalPreview
from app.schemas.guest_fixed_approval import (
    GuestApprovalCandidate,
    GuestApprovalCandidates,
    GuestApprovalPreview,
)
from app.services.artifact_release_snapshot import canonical_bytes, digest
from app.services.fixed_approval import FixedApprovalService
from app.services.guest_approval_scope import (
    POLICY,
    counts,
    external_bases,
    manifest,
    public_bases,
    target_and_nodes,
)
from app.services.publication_access_context import (
    PublicationAccessContext,
    retry_publication_operation,
)


class GuestFixedApprovalService:
    """状態の正本は既存request/eventへ一本化する。"""

    def __init__(self):
        """現行の公開・承認・認可を再利用する。"""
        self.context = PublicationAccessContext()
        self.access = PublicationAccessRepository()
        self.repository = GuestFixedApprovalRepository()
        self.approvals = FixedApprovalService()

    @staticmethod
    def enabled():
        """新しい申請・判断のみを停止できる。"""
        if not settings.guest_fixed_approvals_enabled:
            raise ForbiddenError()

    def related_user(self, original, project_id, grant_id):
        """資格境界より先に取得するFK対象のIDだけを予備解決する。"""
        g, p = self.access.grants, self.access.participations
        with original.get_bind().engine.connect() as connection:
            return connection.scalar(
                select(p.c.user_id)
                .join(g, g.c.participation_id == p.c.id)
                .where(
                    g.c.id == grant_id,
                    g.c.project_id == project_id,
                    p.c.project_id == project_id,
                )
            )

    def permissions(self, db, actor, project_id, release, target_kind):
        """新しい外部依頼の管理に必要な既存権限を再確認する。"""
        for permission in (
            "artifact_release:read",
            "artifact_release:create",
            "requirement:read",
            "requirement:review",
            "artifact_publication:publish",
        ):
            self.context.permission(db, actor, project_id, permission)
        self.approvals.releases._sources(db, actor, project_id, release.source_kinds)
        if target_kind in {"specification", "test_design"}:
            self.context.permission(
                db,
                actor,
                project_id,
                "document:update"
                if target_kind == "specification"
                else "test_plan:update",
            )

    @contextmanager
    def internal(self, original, user, project_id, *, related=()):
        """FK先行取得と公開境界を用い、内部依頼用RRを流用しない。"""
        with self.context.transaction(
            original, user, project_id, write=True, related_user_ids=related
        ) as (db, actor):
            yield db, actor

    def source(self, db, actor, project_id, data):
        """公開中の同Projectの全固定構成を確認する。"""
        publication = self.access.publication(db, project_id, data.publication_id)
        if publication is None or publication.publication_state != "published":
            raise NotFoundError()
        release, nodes, target = target_and_nodes(
            db, actor, project_id, publication, data.target_node_id
        )
        self.permissions(db, actor, project_id, release, target.node_kind)
        return publication, release, nodes, target

    def recipients(self, db, actor, project_id, publication_id, *, grant_id=None, q=""):
        """既存の公開許可を持つ別の本人だけを候補にする。"""
        g, part = self.access.grants, self.access.participations
        candidates = self.access.candidates(
            project_id, func.clock_timestamp(), ""
        ).subquery()
        query = (
            select(
                g.c.id.label("grant_id"),
                part.c.id.label("participation_id"),
                User.id.label("user_id"),
                User.name,
                func.least(g.c.expires_at, part.c.expires_at).label("expires_at"),
                g.c.version.label("grant_version"),
                part.c.version.label("participant_version"),
            )
            .join(part, part.c.id == g.c.participation_id)
            .join(User, User.id == part.c.user_id)
            .where(
                g.c.project_id == project_id,
                g.c.publication_id == publication_id,
                g.c.status == "active",
                g.c.expires_at > func.clock_timestamp(),
                part.c.id.in_(select(candidates.c.id)),
                User.id != actor.id,
                User.password_change_required.is_(False),
                User.name.icontains(q, autoescape=True),
            )
        )
        if grant_id:
            query = query.where(g.c.id == grant_id)
        return list(db.execute(query.order_by(User.name, g.c.id)).mappings())

    @retry_publication_operation
    def candidates(self, original, user, project_id, data, q, page, page_size):
        """候補と件数を同じ認可集合から取得する。"""
        with self.internal(original, user, project_id) as (db, actor):
            self.source(db, actor, project_id, data)
            rows = self.recipients(db, actor, project_id, data.publication_id, q=q)
            return GuestApprovalCandidates(
                items=[
                    GuestApprovalCandidate(
                        grant_id=r["grant_id"],
                        name=r["name"],
                        expires_at=r["expires_at"],
                    )
                    for r in rows[(page - 1) * page_size : page * page_size]
                ],
                total=len(rows),
                page=page,
                page_size=page_size,
            )

    @retry_publication_operation
    def basis(self, original, user, project_id, data):
        """外部合意の不足を申請前に全件確認する。"""
        from app.services.shared_fixed_approval import basis_page

        with self.internal(original, user, project_id) as (db, actor):
            pub, _, nodes, target = self.source(db, actor, project_id, data)
            bases = external_bases(db, project_id, nodes, target)
            return basis_page(
                public_bases(pub, bases),
                data.cursor,
                data.page_size,
                digest([actor.id, project_id, str(pub.id), str(target.id)]),
            )

    def scope(self, db, actor, project_id, data):
        """指定公開内容と公開する依頼文・例外の構成を封印する。"""
        if len(canonical_bytes(data.model_dump(mode="json"))) > 1024 * 1024:
            raise FixedApprovalTooLargeError()
        pub, release, nodes, target = self.source(db, actor, project_id, data)
        candidates = self.recipients(
            db, actor, project_id, pub.id, grant_id=data.grant_id
        )
        if not candidates:
            raise FixedApprovalScopeConflictError()
        recipient = candidates[0]
        bases = external_bases(db, project_id, nodes, target)
        reverse = {value: key for key, value in pub.source_manifest["id_map"].items()}
        exceptions = {
            str(item.node_id): item.reason.strip() for item in data.exceptions
        }
        missing = {
            reverse[str(item.node_id)] for item in bases if item.state != "agreed"
        }
        if set(exceptions) != missing:
            raise FixedApprovalScopeConflictError()
        for item in bases:
            item.reason = exceptions.get(reverse[str(item.node_id)])
        _, preservation = self.approvals.release(
            db, actor, project_id, release.id, pub.preservation_id
        )
        context = dict(
            public_digest=pub.public_digest,
            target_node_id=str(data.target_node_id),
            target_kind=target.node_kind,
            title=target.payload["title"],
            requester_name=actor.name,
            approver_name=recipient["name"],
            request_note=(data.request_note or "").strip() or None,
            bases=public_bases(pub, bases),
            basis_counts=counts(bases),
            exception_count=len(exceptions),
            policy=POLICY,
        )
        if len(canonical_bytes(context)) > 1024 * 1024:
            raise FixedApprovalTooLargeError()
        public_digest = digest(context)
        scope = manifest(release, target, nodes, preservation, bases)
        scope["public_scope_digest"] = public_digest
        preview_hash = digest(
            dict(
                context=context,
                publication_id=str(pub.id),
                target=str(data.target_node_id),
                grant=str(data.grant_id),
                publication_version=pub.publication_version,
                grant_version=recipient["grant_version"],
                participant_version=recipient["participant_version"],
                expires_at=recipient["expires_at"].isoformat(),
                actor=actor.id,
                session=str(db.info["auth_session"].id),
                scope=scope,
            )
        )
        preview = GuestApprovalPreview(
            publication_id=pub.id,
            target_node_id=data.target_node_id,
            target_kind=target.node_kind,
            title=target.payload["title"],
            approver_name=recipient["name"],
            requester_name=actor.name,
            request_note=context["request_note"],
            exception_count=len(exceptions),
            basis_counts=counts(bases),
            public_scope_digest=public_digest,
            preview_digest=preview_hash,
        )
        return preview, bases, scope, context, recipient, pub, target

    @retry_publication_operation
    def preview(self, original, user, project_id, data):
        """保存せず相手に見える依頼を確認する。"""
        self.enabled()
        with self.internal(original, user, project_id) as (db, actor):
            return self.scope(db, actor, project_id, data)[0]

    @retry_publication_operation
    def create(self, original, user, project_id, data):
        """対応・依頼・根拠・封印・監査を一つのtransactionで保存する。"""
        self.enabled()
        related = self.related_user(original, project_id, data.grant_id)
        with self.internal(
            original, user, project_id, related=(related,) if related else ()
        ) as (db, actor):
            values = data.model_dump(mode="json")
            request_digest = digest(values)
            previous = self.approvals.repository.by_operation(
                db, project_id, actor.id, data.request_id
            )
            if previous:
                if (
                    previous.request_digest != request_digest
                    or previous.policy_version != POLICY
                ):
                    raise DuplicateResourceError()
                return self.approvals.read(db, actor, project_id, previous), False
            preview, bases, scope, context, recipient, pub, target = self.scope(
                db, actor, project_id, data
            )
            if (
                not data.confirm_public_scope
                or preview.preview_digest != data.expected_preview_digest
            ):
                raise FixedApprovalScopeConflictError()
            internal_data = SimpleNamespace(
                release_id=pub.release_id,
                target_node_id=target.id,
                preservation_id=pub.preservation_id,
                approver_id=recipient["user_id"],
                request_note=context["request_note"],
            )
            row = self.approvals.save(
                db,
                actor,
                project_id,
                internal_data,
                FixedApprovalPreview(
                    title=preview.title,
                    target_kind=preview.target_kind,
                    source_id=target.source_id,
                    scope_manifest=scope,
                    scope_digest=digest(scope),
                    preview_digest=preview.preview_digest,
                    basis_counts=preview.basis_counts,
                ),
                bases,
                request_id=data.request_id,
                request_digest=request_digest,
                guest_binding=dict(
                    project_id=project_id,
                    publication_id=pub.id,
                    public_target_node_id=data.target_node_id,
                    participation_id=recipient["participation_id"],
                    grant_id=data.grant_id,
                    projection_version=pub.projection_version,
                    public_scope_digest=preview.public_scope_digest,
                    public_context=context,
                ),
            )
            return self.approvals.read(db, actor, project_id, row), True

    @retry_publication_operation
    def cancel(self, original, user, project_id, approval_id, data):
        """失効後も内部の担当者が履歴を残して取消できる。"""
        with self.internal(original, user, project_id) as (db, actor):
            row = self.approvals.repository.get(db, project_id, approval_id, lock=True)
            binding = self.repository.binding(db, approval_id, project_id)
            if row is None or binding is None:
                raise NotFoundError()
            release, _ = self.approvals.release(
                db, actor, project_id, row.release_id, row.preservation_id
            )
            self.permissions(db, actor, project_id, release, row.target_kind)
            if self.approvals.previous_event(
                self.approvals.repository.events(db, row.id), data
            ):
                return self.approvals.read(db, actor, project_id, row)
            if row.status != "requested" or row.version != data.version:
                raise VersionConflictError(
                    {"version": row.version, "status": row.status}
                )
            if not data.reason.strip():
                raise FixedApprovalScopeConflictError()
            now = db.scalar(select(func.clock_timestamp()))
            row.status, row.version, row.finalized_at = "cancelled", 2, now
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
            self.approvals.audit(db, actor, project_id, row.id, "cancelled")
            db.flush()
            return self.approvals.read(db, actor, project_id, row)
