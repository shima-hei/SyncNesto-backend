"""明示Projectと本人を必須にした公開・shared専用DB取得。"""

from typing import cast

from sqlalchemy import Table, and_, exists, func, insert, not_, select, update

from app.db.base import Base
from app.models.artifact_publication import ArtifactPublication
from app.models.artifact_publication_access import (
    ArtifactPublicationEvent,
    ArtifactPublicationGrant,
    ProjectGuestParticipation,
)


class PublicationAccessRepository:
    """sharedでは汎用unscoped ORMを公開せず、必要列だけをCoreで結合する。"""

    publications = cast(Table, ArtifactPublication.__table__)
    participations = cast(Table, ProjectGuestParticipation.__table__)
    grants = cast(Table, ArtifactPublicationGrant.__table__)
    events = cast(Table, ArtifactPublicationEvent.__table__)

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

    @staticmethod
    def page(db, query, order, page, page_size):
        """同じ認可済み集合から件数とページを取得する。"""
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        return db.execute(
            query.order_by(*order).offset((page - 1) * page_size).limit(page_size)
        ).mappings().all(), total

    def shared_query(self, user_id):
        """本人・所有組織・世代・許可・期限を毎回同じJOINで判定する。"""
        p, g, part = self.publications, self.grants, self.participations
        users, projects, tenants, members = (
            Base.metadata.tables[n]
            for n in ("users", "projects", "tenants", "tenant_members")
        )
        return (
            select(
                p,
                part.c.id.label("participant_id"),
                part.c.version.label("participant_version"),
                g.c.id.label("grant_id"),
                g.c.version.label("grant_version"),
                func.least(g.c.expires_at, part.c.expires_at).label("effective_expiry"),
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
            .join(users, users.c.id == part.c.user_id)
            .join(projects, projects.c.id == p.c.project_id)
            .join(tenants, tenants.c.id == projects.c.tenant_id)
            .where(
                part.c.user_id == user_id,
                part.c.status == "active",
                part.c.expires_at > func.clock_timestamp(),
                g.c.status == "active",
                g.c.expires_at > func.clock_timestamp(),
                p.c.publication_state == "published",
                p.c.sealed.is_(True),
                users.c.is_active.is_(True),
                users.c.deleted_at.is_(None),
                projects.c.deleted_at.is_(None),
                tenants.c.status == "active",
                not_(
                    exists(
                        select(members.c.id).where(
                            members.c.tenant_id == projects.c.tenant_id,
                            members.c.user_id == user_id,
                            members.c.status == "active",
                        )
                    )
                ),
            )
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
