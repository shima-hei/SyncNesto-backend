"""明示Project/招待ID/確認hashに限定したDB操作。"""

from sqlalchemy import func, insert, select, update

from app.models.guest_invitation import (
    ProjectGuestInvitation,
    ProjectGuestInvitationCheck,
    ProjectGuestInvitationEvent,
)
from app.models.user import User


class GuestInvitationRepository:
    """通常所属を要求するORM経路へ外部Identityを混ぜない。"""

    invitations = ProjectGuestInvitation.__table__
    checks = ProjectGuestInvitationCheck.__table__
    events = ProjectGuestInvitationEvent.__table__

    def invitation(self, db, identity, *, project_id=None, lock=False):
        """内部操作はProjectも同時に照合する。"""
        table = self.invitations
        query = select(table).where(table.c.id == identity)
        if project_id is not None:
            query = query.where(table.c.project_id == project_id)
        if lock:
            query = query.with_for_update()
        return db.execute(query).mappings().first()

    def check(self, db, identity, proof_hash):
        """確認Cookieだけでは他の招待を参照できない。"""
        t = self.checks
        return (
            db.execute(
                select(t).where(
                    t.c.invitation_id == identity, t.c.proof_hash == proof_hash
                )
            )
            .mappings()
            .first()
        )

    @staticmethod
    def update(db, table, identity, **values):
        """事前に範囲とversionを照合した対象だけを更新する。"""
        db.execute(update(table).where(table.c.id == identity).values(**values))

    @staticmethod
    def add(db, table, **values):
        """呼出元transactionへ追記し、勝手にcommitしない。"""
        db.execute(insert(table).values(**values))

    def previous(self, db, identity, operation):
        """同じ招待の同一操作だけを冪等再送にする。"""
        t = self.events
        return (
            db.execute(
                select(t).where(
                    t.c.invitation_id == identity,
                    t.c.operation_id == operation,
                    t.c.is_root.is_(True),
                )
            )
            .mappings()
            .first()
        )

    def page(self, db, project_id, page, page_size, *, identity=None):
        """管理権限を確認したProjectの集合だけを返す。"""
        table = self.invitations if identity is None else self.events
        query = select(table).where(table.c.project_id == project_id)
        if identity is not None:
            query = query.where(table.c.invitation_id == identity)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        order = table.c.created_at if identity is None else table.c.occurred_at
        rows = (
            db.execute(
                query.order_by(order.desc(), table.c.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .mappings()
            .all()
        )
        return rows, total

    @staticmethod
    def identities(db, email):
        """大小文字の類似候補は自動的に別Identityとして作らない。"""
        return db.scalars(
            select(User)
            .where(func.lower(User.email) == email.lower())
            .order_by(User.id)
        ).all()
