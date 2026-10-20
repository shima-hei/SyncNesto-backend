"""現在許可された指定本人が、公開された固定内容だけを判断する。"""

from collections import Counter
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select, text, update

from app.core.exceptions import (
    FixedApprovalScopeConflictError,
    MfaRequiredError,
    NotFoundError,
    VersionConflictError,
)
from app.models.artifact_release import ArtifactRelease
from app.models.fixed_approval import FixedApprovalEvent
from app.models.session import UserSession
from app.repositories.guest_fixed_approval import GuestFixedApprovalRepository
from app.schemas.guest_fixed_approval import (
    GuestApprovalBases,
    GuestApprovalBasis,
    SharedApprovalDecisionPreview,
    SharedApprovalPage,
    SharedApprovalRead,
)
from app.services.artifact_release_snapshot import (
    MAX_PAGE_BYTES,
    digest,
)
from app.services.fixed_approval import FixedApprovalService
from app.services.fixed_approval_cursor import decode_cursor, encode_cursor
from app.services.fixed_approval_notification import emit_fixed_approval
from app.services.guest_approval_scope import compare_current
from app.services.guest_fixed_approval import GuestFixedApprovalService
from app.services.guest_invitation import GuestInvitationService
from app.services.publication_access_context import retry_publication_operation
from app.services.shared_publication import SharedPublicationService


def basis_page(items, cursor, page_size, authorization):
    """全体構成と現在資格を束縛して1MiB以下へ分割する。"""
    scope = digest([authorization, items])
    offset = decode_cursor(cursor, 0, "guest-approval-basis", scope)
    if offset > len(items):
        raise FixedApprovalScopeConflictError()
    result = GuestApprovalBases(
        items=[],
        total=len(items),
        counts=dict(Counter(item["state"] for item in items)),
        scope_digest=scope,
        next_cursor=None,
    )
    for end in range(offset + 1, min(offset + page_size, len(items)) + 1):
        proposed = result.model_copy(
            update=dict(
                items=[
                    GuestApprovalBasis.model_validate(item)
                    for item in items[offset:end]
                ],
                next_cursor=encode_cursor(0, "guest-approval-basis", scope, end)
                if end < len(items)
                else None,
            )
        )
        if len(proposed.model_dump_json().encode()) > MAX_PAGE_BYTES:
            if not result.items:
                raise FixedApprovalScopeConflictError()
            break
        result = proposed
    return result


class SharedFixedApprovalService:
    """組織や通常Project権限を与えず本人の今回の判断だけを保存する。"""

    def __init__(self):
        """既存の共有認可・固定判断と新しい不変対応を再利用する。"""
        self.shared = SharedPublicationService()
        self.repository = GuestFixedApprovalRepository()
        self.approvals = FixedApprovalService()

    @staticmethod
    def mfa(db, actor):
        """global LOCAL設定とは独立して、このsessionの実MFAを要求する。"""
        if not GuestInvitationService.mfa_verified(db, actor):
            raise MfaRequiredError()

    def get(self, db, original, actor, publication_id, approval_id, *, write=False):
        """現在の固定された許可を待機後に再確認し、正本を返す。"""
        binding = self.repository.binding(db, approval_id)
        if binding is None or binding["publication_id"] != publication_id:
            raise NotFoundError()
        if not db.scalar(
            self.repository.visible(actor.id)
            .where(self.repository.requests.c.id == approval_id)
            .with_only_columns(self.repository.requests.c.id)
        ):
            raise NotFoundError()
        db.execute(
            text("SELECT publication_lock(:project,:reading)"),
            {"project": binding["project_id"], "reading": not write},
        )
        self.shared.context.verify(db, original, actor, internal=False)
        publication, revision = self.shared._get(db, original, actor, publication_id)
        if (
            publication["grant_id"] != binding["grant_id"]
            or publication["participant_id"] != binding["participation_id"]
        ):
            raise NotFoundError()
        row = self.repository.request(db, approval_id, publication_id, lock=write)
        if (
            row is None
            or row["approver_id"] != actor.id
            or not db.scalar(
                text("SELECT guest_approval_has_access(:target,:actor)"),
                {"target": approval_id, "actor": actor.id},
            )
        ):
            raise NotFoundError()
        self.mfa(db, actor)
        return row, binding, publication, revision

    def read(self, db, actor, row, binding, publication, revision):
        """内部event/manifestをそのまま外部へ返さない。"""
        context = binding["public_context"]
        events = self.approvals.repository.events(db, row["id"])
        reason = next(
            (
                event.reason
                for event in events
                if event.seq == 2
                and event.actor_id == actor.id
                and event.action != "cancelled"
            ),
            None,
        )
        return SharedApprovalRead(
            id=row["id"],
            publication_id=binding["publication_id"],
            target_node_id=binding["public_target_node_id"],
            target_kind=row["target_kind"],
            title=context["title"],
            requester_name=context["requester_name"],
            request_note=context["request_note"],
            status=row["status"],
            version=row["version"],
            created_at=row["created_at"],
            captured_at=publication["captured_at"],
            finalized_at=row["finalized_at"],
            decision_reason=reason,
            exception_count=context["exception_count"],
            can_decide=row["status"] == "requested" and settings_enabled(),
            expires_at=publication["effective_expiry"],
            authorization_revision=revision,
            public_scope_digest=binding["public_scope_digest"],
        )

    @retry_publication_operation
    def metadata(self, original, user, publication_id, approval_id):
        """失効後には判断済みの今回の履歴も返さない。"""
        with self.shared.context.transaction(original, user) as (db, actor):
            return self.read(
                db, actor, *self.get(db, original, actor, publication_id, approval_id)
            )

    @retry_publication_operation
    def basis(self, original, user, publication_id, approval_id, cursor, page_size):
        """自分の文書依頼に明示公開した要約だけを読む。"""
        with self.shared.context.transaction(original, user) as (db, actor):
            row, binding, _, revision = self.get(
                db, original, actor, publication_id, approval_id
            )
            return basis_page(
                binding["public_context"]["bases"],
                cursor,
                page_size,
                digest([str(row["id"]), revision, binding["public_scope_digest"]]),
            )

    @retry_publication_operation
    def listing(
        self, original, user, q, status, cursor, page_size, publication_id=None
    ):
        """現在読める自分の依頼だけの集合と件数を作る。"""
        with self.shared.context.transaction(original, user) as (db, actor):
            query = self.repository.visible(actor.id)
            projects = sorted(
                set(
                    db.scalars(
                        query.with_only_columns(self.repository.requests.c.project_id)
                    )
                )
            )
            for project in projects:
                db.execute(
                    text("SELECT publication_lock(:project,true)"), {"project": project}
                )
            self.shared.context.verify(db, original, actor, internal=False)
            self.mfa(db, actor)
            r = self.repository.requests
            query = self.repository.visible(actor.id).where(
                r.c.project_id.in_(projects),
                r.c.scope_manifest["title"].as_string().icontains(q, autoescape=True),
            )
            if status:
                query = query.where(r.c.status == status)
            if publication_id:
                query = query.where(
                    self.repository.bindings.c.publication_id == publication_id
                )
            rows = (
                db.execute(query.order_by(r.c.created_at.desc(), r.c.id))
                .mappings()
                .all()
            )
            scope = digest(
                [
                    actor.id,
                    str(db.info["auth_session"].id),
                    q,
                    status,
                    str(publication_id) if publication_id else None,
                    [[str(row["id"]), str(row["publication_id"])] for row in rows],
                ]
            )
            # 状態・許可の世代も集合へ束縛する。
            values = [
                self.read(
                    db,
                    actor,
                    *self.get(db, original, actor, item["publication_id"], item["id"]),
                )
                for item in rows
            ]
            scope = digest(
                [
                    scope,
                    [
                        [str(item.id), item.version, item.authorization_revision]
                        for item in values
                    ],
                ]
            )
            offset = decode_cursor(cursor, 0, "guest-approval-list", scope)
            if offset > len(values):
                raise FixedApprovalScopeConflictError()
            end = offset + page_size
            return SharedApprovalPage(
                items=values[offset:end],
                total=len(values),
                next_cursor=encode_cursor(0, "guest-approval-list", scope, end)
                if end < len(values)
                else None,
            )

    def review(self, db, actor, row, binding, publication, revision):
        """固定内容と現在比較を、短期の署名対象へ束縛する。"""
        if row["status"] != "requested":
            raise VersionConflictError(
                {"version": row["version"], "status": row["status"]}
            )
        release = db.scalar(
            select(ArtifactRelease).where(
                ArtifactRelease.id == row["release_id"],
                ArtifactRelease.project_id == row["project_id"],
            )
        )
        if release is None:
            raise NotFoundError()
        comparison = compare_current(db, row["project_id"], release)
        scope = digest(
            [
                str(row["id"]),
                row["version"],
                binding["public_scope_digest"],
                revision,
                comparison,
            ]
        )
        return comparison, scope

    @retry_publication_operation
    def preview(self, original, user, publication_id, approval_id):
        """判断準備は保存せず、今回の本人sessionへ確認を束縛する。"""
        GuestFixedApprovalService.enabled()
        with self.shared.context.transaction(original, user) as (db, actor):
            row, binding, publication, revision = self.get(
                db, original, actor, publication_id, approval_id
            )
            comparison, scope = self.review(
                db, actor, row, binding, publication, revision
            )
            now = db.scalar(select(func.clock_timestamp()))
            assert now is not None
            session = db.info["auth_session"]
            deadline = min(
                now + timedelta(minutes=5),
                publication["effective_expiry"],
                session.expires_at,
                session.absolute_expires_at,
            )
            return SharedApprovalDecisionPreview(
                version=row["version"],
                review_proof=encode_cursor(
                    0, "guest-approval-review", scope, int(deadline.timestamp())
                ),
                expires_at=deadline,
                body_comparison=comparison,
                exception_count=binding["public_context"]["exception_count"],
            )

    @retry_publication_operation
    def decide(self, original, user, publication_id, approval_id, data):
        """公開停止と二重判断を直列化し、本人判断と監査を一括commitする。"""
        GuestFixedApprovalService.enabled()
        with original.get_bind().engine.connect() as connection:
            binding = self.repository.binding(connection, approval_id)
        if binding is None or binding["publication_id"] != publication_id:
            raise NotFoundError()
        with self.shared.context.transaction(
            original, user, write=True, related_project_ids=(binding["project_id"],)
        ) as (db, actor):
            row, binding, publication, revision = self.get(
                db, original, actor, publication_id, approval_id, write=True
            )
            db.scalar(
                select(UserSession)
                .where(UserSession.id == db.info["auth_session"].id)
                .with_for_update(read=True)
            )
            row, binding, publication, revision = self.get(
                db, original, actor, publication_id, approval_id, write=True
            )
            if self.approvals.previous_event(
                self.approvals.repository.events(db, row["id"]), data
            ):
                return self.read(db, actor, row, binding, publication, revision)
            if data.version != row["version"]:
                raise VersionConflictError(
                    {"version": row["version"], "status": row["status"]}
                )
            comparison, scope = self.review(
                db, actor, row, binding, publication, revision
            )
            deadline = decode_cursor(
                data.review_proof, 0, "guest-approval-review", scope
            )
            now = db.scalar(select(func.clock_timestamp()))
            assert now is not None
            if (
                deadline <= now.timestamp()
                or not data.confirm_fixed_scope
                or (comparison != "equal" and not data.confirm_changed_or_unknown)
                or (
                    binding["public_context"]["exception_count"]
                    and not data.confirm_exceptions
                )
            ):
                raise FixedApprovalScopeConflictError()
            reason = (data.comment or "").strip() or None
            if data.decision == "reject" and reason is None:
                raise FixedApprovalScopeConflictError()
            status = (
                "rejected"
                if data.decision == "reject"
                else (
                    "approved_with_exceptions"
                    if binding["public_context"]["exception_count"]
                    else "approved"
                )
            )
            db.execute(
                update(self.repository.requests)
                .where(
                    self.repository.requests.c.id == row["id"],
                    self.repository.requests.c.project_id == row["project_id"],
                )
                .values(status=status, version=2, finalized_at=now)
            )
            db.add(
                FixedApprovalEvent(
                    id=uuid4(),
                    approval_id=row["id"],
                    seq=2,
                    operation_id=data.operation_id,
                    operation_digest=digest(data.model_dump(mode="json")),
                    actor_id=actor.id,
                    action=status,
                    reason=reason,
                    scope_digest=row["scope_digest"],
                    observation=dict(
                        public_scope_digest=binding["public_scope_digest"],
                        publication_id=str(publication_id),
                        participation_id=str(binding["participation_id"]),
                        grant_id=str(binding["grant_id"]),
                        authorization_revision=revision,
                        body_comparison=comparison,
                    ),
                )
            )
            self.approvals.audit(db, actor, row["project_id"], row["id"], status)
            db.flush()
            emit_fixed_approval(db, row["id"], status)
            final = self.repository.request(db, approval_id, publication_id)
            return self.read(db, actor, final, binding, publication, revision)


def settings_enabled():
    """停止時には履歴を読めても新たな判断を案内しない。"""
    from app.core.config import settings

    return settings.guest_fixed_approvals_enabled
