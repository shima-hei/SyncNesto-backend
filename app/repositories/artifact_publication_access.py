"""明示Projectと本人を必須にした公開・shared専用DB取得。"""

from typing import cast

from sqlalchemy import (
    Date,
    DateTime,
    Table,
    and_,
    case,
    exists,
    func,
    insert,
    literal,
    not_,
    or_,
    select,
    union_all,
    update,
)
from sqlalchemy import cast as sql_cast

from app.db.base import Base
from app.models.artifact_publication import ArtifactPublication
from app.models.artifact_publication_access import (
    ArtifactPublicationEvent,
    ArtifactPublicationGrant,
    GuestProjectProfile,
    ProjectGuestParticipation,
)


class PublicationAccessRepository:
    """sharedでは汎用unscoped ORMを公開せず、必要列だけをCoreで結合する。"""

    publications = cast(Table, ArtifactPublication.__table__)
    participations = cast(Table, ProjectGuestParticipation.__table__)
    grants = cast(Table, ArtifactPublicationGrant.__table__)
    events = cast(Table, ArtifactPublicationEvent.__table__)
    profiles = cast(Table, GuestProjectProfile.__table__)

    def publication(self, db, project_id, identity):
        """社内の同一Projectの封印版だけを返す。"""
        return db.scalar(
            select(ArtifactPublication).where(
                ArtifactPublication.project_id == project_id,
                ArtifactPublication.id == identity,
                ArtifactPublication.sealed.is_(True),
            )
        )

    def participation(self, db, project_id, identity):
        """管理メタデータとして対象世代を取得する。"""
        t = self.participations
        return (
            db.execute(
                select(t).where(t.c.project_id == project_id, t.c.id == identity)
            )
            .mappings()
            .first()
        )

    def grant(self, db, project_id, publication_id, identity):
        """URLの全親IDを照合して許可を取得する。"""
        t = self.grants
        return (
            db.execute(
                select(t).where(
                    t.c.project_id == project_id,
                    t.c.publication_id == publication_id,
                    t.c.id == identity,
                )
            )
            .mappings()
            .first()
        )

    def previous(self, db, project_id, actor_id, request_id):
        """同じ本人・Projectの根操作だけで冪等性を解決する。"""
        t = self.events
        return (
            db.execute(
                select(t).where(
                    t.c.project_id == project_id,
                    t.c.actor_id == actor_id,
                    t.c.request_id == request_id,
                    t.c.is_root.is_(True),
                )
            )
            .mappings()
            .first()
        )

    def root(self, db, values):
        """DB側の子イベントと同一transactionで操作結果を追記する。"""
        db.execute(insert(self.events).values(**values))

    def update_publication(self, db, project_id, identity, state, version):
        """本文に触れず公開状態だけを進める。"""
        db.execute(
            update(self.publications)
            .where(
                self.publications.c.project_id == project_id,
                self.publications.c.id == identity,
            )
            .values(publication_state=state, publication_version=version)
        )

    def add_grant(self, db, values):
        """複合FKとDBガードで同一Projectの許可を追加する。"""
        db.execute(insert(self.grants).values(**values))

    def revoke(self, db, table, identity, actor_id, reason, now):
        """対象は事前に同一Projectで解決した行に限定する。"""
        db.execute(
            update(table)
            .where(table.c.id == identity)
            .values(
                status="revoked",
                version=table.c.version + 1,
                revoked_at=now,
                revoked_by=actor_id,
                revocation_reason=reason,
            )
        )

    def change_participation_expiry(self, db, project_id, identity, expires_at):
        part = self.participations
        db.execute(
            update(part)
            .where(part.c.project_id == project_id, part.c.id == identity)
            .values(expires_at=expires_at, version=part.c.version + 1)
        )

    def candidates(self, project_id, now, q):
        """有効な本人資格だけを検索する。全usersの検索には使わない。"""
        part = self.participations
        users, projects, members = (
            Base.metadata.tables[n] for n in ("users", "projects", "tenant_members")
        )
        reviews = Base.metadata.tables["review_account_grants"]
        return (
            select(part)
            .join(users, users.c.id == part.c.user_id)
            .join(projects, projects.c.id == part.c.project_id)
            .where(
                part.c.project_id == project_id,
                part.c.status == "active",
                part.c.expires_at > now,
                users.c.is_active.is_(True),
                users.c.deleted_at.is_(None),
                not_(
                    exists(
                        select(reviews.c.user_id).where(
                            reviews.c.user_id == part.c.user_id
                        )
                    )
                ),
                not_(
                    exists(
                        select(members.c.id).where(
                            members.c.user_id == part.c.user_id,
                            members.c.tenant_id == projects.c.tenant_id,
                            members.c.status == "active",
                        )
                    )
                ),
                part.c.display_label.icontains(q, autoescape=True),
            )
        )

    def participations_query(self, project_id):
        """参加管理一覧は資料内容・公開件数を結合しない。"""
        return select(self.participations).where(
            self.participations.c.project_id == project_id
        )

    def grants_query(self, project_id, publication_id):
        """内部の許可履歴に参加時ラベルを添える。"""
        t, part = self.grants, self.participations
        return (
            select(t, part.c.display_label)
            .join(
                part,
                and_(
                    part.c.id == t.c.participation_id,
                    part.c.project_id == t.c.project_id,
                ),
            )
            .where(t.c.project_id == project_id, t.c.publication_id == publication_id)
        )

    def participant_grants_query(
        self, project_id, participation_id, source_kinds, read_documents
    ):
        grant, part, publication = self.grants, self.participations, self.publications
        query = (
            select(
                grant,
                part.c.display_label,
                publication.c.title.label("publication_title"),
                publication.c.captured_at,
                part.c.expires_at.label("participation_expires_at"),
                part.c.user_id.label("participation_user_id"),
            )
            .join(
                part,
                and_(
                    part.c.id == grant.c.participation_id,
                    part.c.project_id == grant.c.project_id,
                ),
            )
            .join(
                publication,
                and_(
                    publication.c.id == grant.c.publication_id,
                    publication.c.project_id == grant.c.project_id,
                ),
            )
            .where(
                grant.c.project_id == project_id,
                grant.c.participation_id == participation_id,
                publication.c.sealed.is_(True),
                publication.c.source_kinds.contained_by(source_kinds),
            )
        )
        if not read_documents:
            query = query.where(publication.c.preservation_id.is_(None))
        return query

    def events_query(self, project_id, publication_id):
        """本人別失効も含め、その公開版の履歴だけを返す。"""
        users = Base.metadata.tables["users"]
        return (
            select(self.events, users.c.name.label("actor_label"))
            .outerjoin(users, users.c.id == self.events.c.actor_id)
            .where(
                self.events.c.project_id == project_id,
                self.events.c.publication_id == publication_id,
            )
        )

    def participant_decisions_query(self, grant_query, source_kinds, read_documents):
        """公開許可と判断の正本を現在読める範囲で結合する。"""
        columns = grant_query.selected_columns
        grant = grant_query.with_only_columns(
            columns.id,
            columns.project_id,
            columns.participation_id,
            columns.participation_user_id,
            columns.publication_id,
            columns.publication_title,
            columns.captured_at,
            case(
                (columns.status == "revoked", "revoked"),
                (
                    func.least(columns.expires_at, columns.participation_expires_at)
                    <= func.clock_timestamp(),
                    "expired",
                ),
                else_="active",
            ).label("access_state"),
        ).cte("participant_decision_grants")
        tables = Base.metadata.tables
        work = tables["requirement_workflow_requests"]
        target = tables["requirement_workflow_participants"]
        work_binding = tables["requirement_workflow_guest_bindings"]
        fixed = tables["fixed_approval_requests"]
        fixed_binding = tables["fixed_approval_guest_bindings"]
        node = tables["artifact_publication_nodes"]
        release = tables["artifact_releases"]
        preservation = tables["artifact_preservations"]

        def matches(binding):
            return and_(
                binding.c.grant_id == grant.c.id,
                binding.c.project_id == grant.c.project_id,
                binding.c.publication_id == grant.c.publication_id,
                binding.c.participation_id == grant.c.participation_id,
            )

        def readable_release(request):
            return and_(
                release.c.id == request.c.release_id,
                release.c.project_id == grant.c.project_id,
                release.c.sealed.is_(True),
                release.c.source_kinds.contained_by(source_kinds),
            )

        public_fields = (
            grant.c.publication_id,
            func.coalesce(
                node.c.payload["title"].astext, grant.c.publication_title
            ).label("title"),
            grant.c.captured_at,
            grant.c.access_state,
        )
        workflow = (
            select(
                work.c.id,
                *public_fields,
                work.c.kind,
                work.c.scope_kind.label("target_kind"),
                work.c.status.label("state"),
                target.c.status.label("response_state"),
                work.c.created_at,
                target.c.responded_at,
                work.c.due_on,
                sql_cast(None, DateTime(timezone=True)).label("decision_expires_at"),
            )
            .select_from(grant)
            .join(work_binding, matches(work_binding))
            .join(
                node,
                and_(
                    node.c.publication_id == grant.c.publication_id,
                    node.c.id == work_binding.c.target_node_id,
                ),
            )
            .join(
                work,
                and_(
                    work.c.id == work_binding.c.request_id,
                    work.c.project_id == grant.c.project_id,
                ),
            )
            .join(release, readable_release(work))
            .join(
                target,
                and_(
                    target.c.request_id == work_binding.c.request_id,
                    target.c.user_id == work_binding.c.user_id,
                    target.c.user_id == grant.c.participation_user_id,
                ),
            )
        )
        approvals = (
            select(
                fixed.c.id,
                *public_fields,
                literal("fixed_approval").label("kind"),
                fixed.c.target_kind,
                fixed.c.status.label("state"),
                case(
                    (fixed.c.status.in_(["requested", "cancelled"]), None),
                    else_=fixed.c.status,
                ).label("response_state"),
                fixed.c.created_at,
                case(
                    (fixed.c.status == "cancelled", None),
                    else_=fixed.c.finalized_at,
                ).label("responded_at"),
                sql_cast(None, Date).label("due_on"),
                sql_cast(
                    fixed_binding.c.public_context["decision_expires_at"].astext,
                    DateTime(timezone=True),
                ).label("decision_expires_at"),
            )
            .select_from(grant)
            .join(fixed_binding, matches(fixed_binding))
            .join(
                node,
                and_(
                    node.c.publication_id == grant.c.publication_id,
                    node.c.id == fixed_binding.c.public_target_node_id,
                ),
            )
            .join(
                fixed,
                and_(
                    fixed.c.id == fixed_binding.c.approval_id,
                    fixed.c.project_id == grant.c.project_id,
                    fixed.c.approver_id == grant.c.participation_user_id,
                ),
            )
            .join(release, readable_release(fixed))
            .where(fixed.c.sealed.is_(True))
        )
        if read_documents:
            approvals = approvals.where(
                or_(
                    fixed.c.preservation_id.is_(None),
                    exists().where(
                        preservation.c.id == fixed.c.preservation_id,
                        preservation.c.release_id == release.c.id,
                        preservation.c.status == "ready",
                    ),
                )
            )
        else:
            approvals = approvals.where(fixed.c.preservation_id.is_(None))
        return select(union_all(workflow, approvals).subquery())

    @staticmethod
    def page(db, query, order, page, page_size):
        """同じ認可済み集合から件数とページを取得する。"""
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        return db.execute(
            query.order_by(*order).offset((page - 1) * page_size).limit(page_size)
        ).mappings().all(), total

    def current_participations(self, user_id=None):
        """参加資格を単独で評価し、資料の許可と共通で使う。"""
        part = self.participations
        users, projects, tenants, members = (
            Base.metadata.tables[n]
            for n in ("users", "projects", "tenants", "tenant_members")
        )
        query = (
            select(part)
            .join(users, users.c.id == part.c.user_id)
            .join(projects, projects.c.id == part.c.project_id)
            .join(tenants, tenants.c.id == projects.c.tenant_id)
            .where(
                part.c.status == "active",
                part.c.expires_at > func.clock_timestamp(),
                users.c.is_active.is_(True),
                users.c.deleted_at.is_(None),
                projects.c.deleted_at.is_(None),
                tenants.c.status == "active",
                not_(
                    exists(
                        select(members.c.id).where(
                            members.c.tenant_id == projects.c.tenant_id,
                            members.c.user_id == part.c.user_id,
                            members.c.status == "active",
                        )
                    )
                ),
            )
        )
        return query if user_id is None else query.where(part.c.user_id == user_id)

    def shared_query(self, user_id=None):
        """参加資格の上に、公開中の封印版と本人別の許可を要求する。"""
        p, g = self.publications, self.grants
        part = self.current_participations(user_id).subquery()
        return (
            select(
                p,
                part.c.id.label("participant_id"),
                part.c.user_id.label("participant_user_id"),
                part.c.version.label("participant_version"),
                g.c.id.label("grant_id"),
                g.c.version.label("grant_version"),
                g.c.created_at.label("shared_at"),
                g.c.download_allowed,
                g.c.comment_allowed,
                part.c.display_label.label("participant_label"),
                func.least(g.c.expires_at, part.c.expires_at).label("effective_expiry"),
                self.profiles.c.name.label("public_project_name"),
            )
            .join(
                g, and_(g.c.publication_id == p.c.id, g.c.project_id == p.c.project_id)
            )
            .join(
                part,
                and_(
                    part.c.id == g.c.participation_id,
                    part.c.project_id == g.c.project_id,
                ),
            )
            .outerjoin(self.profiles, self.profiles.c.project_id == p.c.project_id)
            .where(
                g.c.status == "active",
                g.c.expires_at > func.clock_timestamp(),
                or_(
                    g.c.decision_only.is_(False),
                    self._has_decision("requirement_workflow", "request_id"),
                    self._has_decision("fixed_approval", "approval_id"),
                ),
                p.c.publication_state == "published",
                p.c.sealed.is_(True),
            )
        )

    def guest_workflow_visibility(self, actor_id):
        """要件の依頼を本人と同じ公開許可の世代に限定する。"""
        bindings = Base.metadata.tables["requirement_workflow_guest_bindings"]
        publications = self.shared_query(actor_id).subquery()
        return (
            select(
                bindings.c.request_id,
                bindings.c.publication_id,
                bindings.c.project_id,
                bindings.c.target_node_id,
                publications.c.effective_expiry,
                publications.c.public_project_name,
                publications.c.captured_at,
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

    def _has_decision(self, workflow, request_fk):
        binding = Base.metadata.tables[f"{workflow}_guest_bindings"]
        request = Base.metadata.tables[f"{workflow}_requests"]
        grant = self.grants
        return exists(
            select(binding.c.grant_id)
            .join(request, request.c.id == binding.c[request_fk])
            .where(
                binding.c.grant_id == grant.c.id,
                binding.c.project_id == grant.c.project_id,
                binding.c.publication_id == grant.c.publication_id,
                binding.c.participation_id == grant.c.participation_id,
                request.c.project_id == grant.c.project_id,
                request.c.status != "cancelled",
            )
            .correlate(grant)
        )

    def shared_projects_query(self, user_id):
        """公開概要と参加世代だけを返す。"""
        part = self.current_participations(user_id).subquery()
        profile = self.profiles
        return select(
            part.c.project_id,
            part.c.id.label("participation_id"),
            part.c.expires_at,
            profile.c.name,
            profile.c.description,
            profile.c.starts_on,
            profile.c.ends_on,
            profile.c.contact,
            profile.c.announcement,
        ).select_from(
            part.outerjoin(profile, profile.c.project_id == part.c.project_id)
        )

    def shared_nodes(self, db, identity, offset, limit):
        """公開DTOのnodeだけを取得する。元固定版や内部sourceを結合しない。"""
        t = Base.metadata.tables["artifact_publication_nodes"]
        return (
            db.execute(
                select(t)
                .where(t.c.publication_id == identity, t.c.ordinal >= offset)
                .order_by(t.c.ordinal)
                .limit(limit)
            )
            .mappings()
            .all()
        )
