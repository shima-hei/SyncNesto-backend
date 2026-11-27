"""外部依頼の明示親と、現在の公開許可を照合するDB操作。"""

from typing import cast

from sqlalchemy import DateTime, Table, and_, exists, func, or_, select
from sqlalchemy import cast as sql_cast

from app.models.fixed_approval import FixedApprovalRequest
from app.models.guest_fixed_approval import FixedApprovalGuestBinding
from app.repositories.artifact_publication_access import PublicationAccessRepository


class GuestFixedApprovalRepository:
    """内部の汎用取得を外部へ開放せず、全親IDをJOINする。"""

    bindings = cast(Table, FixedApprovalGuestBinding.__table__)
    requests = cast(Table, FixedApprovalRequest.__table__)

    def deadline(self):
        """公開依頼の時点で確定した回答期限を返す。"""
        return sql_cast(
            self.bindings.c.public_context["decision_expires_at"].astext,
            DateTime(timezone=True),
        )

    def current_deadline(self):
        """回答済みの履歴を保持し、未回答で期限切れの依頼を除く。"""
        deadline = self.deadline()
        return or_(
            self.requests.c.status != "requested",
            deadline.is_(None),
            deadline > func.clock_timestamp(),
        )

    def binding(self, db, identity, project_id=None):
        """対応を取得する。認可は呼出元で必ず追加する。"""
        b = self.bindings
        query = select(b).where(b.c.approval_id == identity)
        if project_id is not None:
            query = query.where(b.c.project_id == project_id)
        return db.execute(query).mappings().first()

    def request(self, db, identity, publication_id, *, lock=False):
        """指定公開版と同Projectの封印済み正本だけを読む。"""
        r, b = self.requests, self.bindings
        query = (
            select(r)
            .join(b, and_(b.c.approval_id == r.c.id, b.c.project_id == r.c.project_id))
            .where(
                r.c.id == identity,
                b.c.publication_id == publication_id,
                r.c.sealed.is_(True),
            )
        )
        if lock:
            query = query.with_for_update(of=r)
        return db.execute(query).mappings().first()

    def visible(self, user_id):
        """過去世代・再許可では復活しない、本人に指定した依頼集合。"""
        r, b = self.requests, self.bindings
        access = PublicationAccessRepository()
        p, g = access.publications, access.grants
        shared = access.shared_query(user_id)
        allowed = shared.where(
            p.c.id == b.c.publication_id,
            p.c.project_id == b.c.project_id,
            g.c.id == b.c.grant_id,
            shared.selected_columns.participant_id == b.c.participation_id,
        )
        return (
            select(r.c.id, r.c.project_id, r.c.created_at, b.c.publication_id)
            .join(b, and_(b.c.approval_id == r.c.id, b.c.project_id == r.c.project_id))
            .where(
                r.c.approver_id == user_id,
                r.c.sealed.is_(True),
                self.current_deadline(),
                exists(allowed),
            )
        )
