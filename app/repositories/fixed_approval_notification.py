"""固定承認の正本から通知の対象・宛先・現在の閲覧範囲を解決する。"""

from typing import cast

from sqlalchemy import (
    String,
    Table,
    and_,
    case,
    exists,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import insert

from app.db.base import Base
from app.models.notification import Notification, NotificationType
from app.repositories.guest_fixed_approval import GuestFixedApprovalRepository

ACTION_TYPES = {
    "requested": NotificationType.FIXED_APPROVAL_REQUESTED.value,
    "approved": NotificationType.FIXED_APPROVAL_DECIDED.value,
    "approved_with_exceptions": NotificationType.FIXED_APPROVAL_DECIDED.value,
    "rejected": NotificationType.FIXED_APPROVAL_DECIDED.value,
    "cancelled": NotificationType.FIXED_APPROVAL_CANCELLED.value,
}


class FixedApprovalNotificationRepository:
    """JSONの自己申告ではなく正本の親関係と現在権限を必須にする。"""

    def __init__(self):
        """Coreの明示JOINを使い、外部に組織scopeを仮発行しない。"""
        self.n = cast(Table, Notification.__table__)
        self.r, self.e, self.b, self.p, self.release = (
            Base.metadata.tables[name]
            for name in (
                "fixed_approval_requests",
                "fixed_approval_events",
                "fixed_approval_guest_bindings",
                "projects",
                "artifact_releases",
            )
        )

    def permission(self, user_id, code):
        """現在のProject roleのpermissionだけを照合する。"""
        pm, role, rp, perm = (
            Base.metadata.tables[name].alias()
            for name in ("project_members", "roles", "role_permissions", "permissions")
        )
        return exists(
            select(pm.c.id)
            .join(role, role.c.id == pm.c.role_id)
            .join(rp, rp.c.role_id == role.c.id)
            .join(perm, perm.c.id == rp.c.permission_id)
            .where(
                pm.c.user_id == user_id,
                pm.c.project_id == self.r.c.project_id,
                pm.c.deleted_at.is_(None),
                role.c.scope == "project",
                perm.c.code == code,
            )
            .correlate(self.r)
        )

    def internal_access(self, user_id):
        """本文取得と同じ所属・全source・保全readをSQL集合へ適用する。"""
        u, tm, tenant, review = (
            Base.metadata.tables[name].alias()
            for name in ("users", "tenant_members", "tenants", "review_account_grants")
        )
        identity = exists(
            select(u.c.id)
            .join(tm, tm.c.user_id == u.c.id)
            .join(tenant, tenant.c.id == tm.c.tenant_id)
            .where(
                u.c.id == user_id,
                u.c.is_active.is_(True),
                u.c.deleted_at.is_(None),
                u.c.user_type == "internal",
                u.c.password_change_required.is_(False),
                tm.c.tenant_id == self.p.c.tenant_id,
                tm.c.status == "active",
                tenant.c.status == "active",
                self.p.c.deleted_at.is_(None),
                ~exists(select(review.c.user_id).where(review.c.user_id == u.c.id)),
            )
            .correlate(self.p, self.r)
        )
        kinds = self.release.c.source_kinds
        return and_(
            identity,
            self.permission(user_id, "requirement:read"),
            self.permission(user_id, "artifact_release:read"),
            kinds.contained_by(
                ["requirement", "requirement_document", "specification", "test_design"]
            ),
            or_(
                ~kinds.contains(["specification"]),
                self.permission(user_id, "document:read"),
            ),
            or_(
                ~kinds.contains(["test_design"]),
                self.permission(user_id, "test_plan:read"),
            ),
            or_(
                self.r.c.preservation_id.is_(None),
                self.permission(user_id, "document:read"),
            ),
        )

    def base(self):
        """通知の変更不能なevent/対象/actor/tenant対応だけを結合する。"""
        n, r, e, b, p = self.n, self.r, self.e, self.b, self.p
        actor = Base.metadata.tables["users"].alias("notification_actor")
        return (
            select(
                n,
                r.c.id.label("approval_id"),
                e.c.id.label("event_id"),
                e.c.action.label("action"),
                r.c.status.label("current_status"),
                r.c.target_kind.label("target_kind"),
                r.c.scope_manifest.label("fixed_scope"),
                b.c.publication_id.label("publication_id"),
                b.c.public_context.label("public_context"),
                p.c.name.label("project_name"),
                actor.c.name.label("actor_name"),
            )
            .join(e, n.c.event_key == literal("fixed_approval:") + e.c.id.cast(String))
            .join(
                r, and_(r.c.id == e.c.approval_id, n.c.target_id == r.c.id.cast(String))
            )
            .join(
                p,
                and_(
                    p.c.id == r.c.project_id,
                    n.c.project_id == p.c.id,
                    n.c.tenant_id == p.c.tenant_id,
                ),
            )
            .join(self.release, self.release.c.id == r.c.release_id)
            .outerjoin(
                b, and_(b.c.approval_id == r.c.id, b.c.project_id == r.c.project_id)
            )
            .join(actor, actor.c.id == e.c.actor_id)
            .where(
                n.c.target_type == "fixed_approval",
                r.c.sealed.is_(True),
                n.c.actor_user_id == e.c.actor_id,
                n.c.recipient_user_id != e.c.actor_id,
                n.c.type == case(ACTION_TYPES, value=e.c.action),
            )
        )

    def internal(self, user_id, tenant_id):
        """新通知を件数・ページ・既読より前に内部本人の集合へ制限する。"""
        n, r, e, b = self.n, self.r, self.e, self.b
        recipient = or_(
            and_(
                e.c.action == "requested",
                b.c.approval_id.is_(None),
                r.c.approver_id == user_id,
            ),
            and_(
                e.c.action.in_(["approved", "approved_with_exceptions", "rejected"]),
                r.c.requester_id == user_id,
            ),
            and_(
                e.c.action == "cancelled",
                or_(
                    r.c.requester_id == user_id,
                    and_(b.c.approval_id.is_(None), r.c.approver_id == user_id),
                ),
            ),
        )
        return self.base().where(
            n.c.recipient_user_id == user_id,
            n.c.tenant_id == tenant_id,
            recipient,
            self.internal_access(user_id),
        )

    def shared(self, user_id):
        """指定本人と現在の同じ公開世代だけを返す。"""
        visible = GuestFixedApprovalRepository().visible(user_id)
        pub, grant, part = (
            Base.metadata.tables[name].alias()
            for name in (
                "artifact_publications",
                "artifact_publication_grants",
                "project_guest_participations",
            )
        )
        b = self.b
        return (
            self.base()
            .join(pub, pub.c.id == b.c.publication_id)
            .join(
                grant,
                and_(
                    grant.c.id == b.c.grant_id,
                    grant.c.publication_id == pub.c.id,
                    grant.c.project_id == b.c.project_id,
                ),
            )
            .join(
                part,
                and_(
                    part.c.id == b.c.participation_id,
                    part.c.id == grant.c.participation_id,
                    part.c.project_id == b.c.project_id,
                ),
            )
            .add_columns(
                pub.c.publication_version.label("publication_version"),
                part.c.id.label("participant_id"),
                part.c.version.label("participant_version"),
                grant.c.id.label("grant_id"),
                grant.c.version.label("grant_version"),
                func.least(part.c.expires_at, grant.c.expires_at).label(
                    "effective_expiry"
                ),
            )
            .where(
                self.n.c.recipient_user_id == user_id,
                self.r.c.approver_id == user_id,
                self.e.c.action.in_(["requested", "cancelled"]),
                self.r.c.id.in_(visible.with_only_columns(self.r.c.id).correlate(None)),
                func.guest_approval_has_access(self.r.c.id, user_id),
            )
        )

    def emit(self, db, approval_id, action):
        """同じtransactionの正本を検証し、最大2宛先へ一度だけ追記する。"""
        r, e, p, b = self.r, self.e, self.p, self.b
        actor = Base.metadata.tables["users"].alias()
        source = (
            db.execute(
                select(
                    r,
                    e.c.id.label("event_id"),
                    e.c.actor_id.label("actor_id"),
                    actor.c.name.label("actor_name"),
                    p.c.tenant_id.label("tenant_id"),
                    p.c.name.label("project_name"),
                    b.c.public_context.label("public_context"),
                )
                .join(e, and_(e.c.approval_id == r.c.id, e.c.action == action))
                .join(p, p.c.id == r.c.project_id)
                .join(actor, actor.c.id == e.c.actor_id)
                .outerjoin(b, b.c.approval_id == r.c.id)
                .where(r.c.id == approval_id, r.c.sealed.is_(True))
            )
            .mappings()
            .one()
        )
        recipients = (
            {source["approver_id"]}
            if action == "requested"
            else {source["requester_id"], source["approver_id"]}
            if action == "cancelled"
            else {source["requester_id"]}
        ) - {source["actor_id"]}
        for recipient in recipients:
            guest = (
                source["public_context"] is not None
                and recipient == source["approver_id"]
            )
            if guest:
                permitted = db.scalar(
                    GuestFixedApprovalRepository()
                    .visible(recipient)
                    .where(r.c.id == approval_id)
                    .with_only_columns(r.c.id)
                )
            else:
                permitted = db.scalar(
                    select(r.c.id)
                    .join(p, p.c.id == r.c.project_id)
                    .join(self.release, self.release.c.id == r.c.release_id)
                    .where(r.c.id == approval_id, self.internal_access(recipient))
                )
            if not permitted:
                continue
            public = source["public_context"] or {}
            snapshot = dict(
                actor_name=public.get("requester_name", "依頼元")
                if guest
                else source["actor_name"],
                project_name="共有された内容" if guest else source["project_name"],
                target_title=public["title"]
                if guest
                else source["scope_manifest"]["title"],
            )
            db.execute(
                insert(self.n)
                .values(
                    tenant_id=source["tenant_id"],
                    recipient_user_id=recipient,
                    type=ACTION_TYPES[action],
                    actor_user_id=source["actor_id"],
                    project_id=source["project_id"],
                    target_type="fixed_approval",
                    target_id=str(approval_id),
                    event_key=f"fixed_approval:{source['event_id']}",
                    snapshot=snapshot,
                    context=dict(
                        approval_id=str(approval_id),
                        event_id=str(source["event_id"]),
                        action=action,
                    ),
                )
                .on_conflict_do_nothing(constraint="uq_notification_event_recipient")
            )
