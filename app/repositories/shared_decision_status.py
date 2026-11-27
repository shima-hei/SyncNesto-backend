"""表示中の固定版で本人が対応する状態だけをまとめる。"""

from sqlalchemy import DateTime, and_, cast, func, or_, select, union_all

from app.db.base import Base
from app.repositories.guest_fixed_approval import GuestFixedApprovalRepository
from app.repositories.requirement_workflow import GUEST_DUE_ZONE


def shared_decision_status(db, access, user_id, publication_ids):
    """現在閲覧できる依頼から、一覧に必要な状態と再確認時刻を集計する。"""
    work = Base.metadata.tables["requirement_workflow_requests"]
    participant = Base.metadata.tables["requirement_workflow_participants"]
    visible = access.guest_workflow_visibility(user_id).subquery()
    deadline = func.timezone(GUEST_DUE_ZONE.key, cast(work.c.due_on + 1, DateTime()))
    waiting = and_(work.c.status == "requested", participant.c.status == "pending")
    workflow = (
        select(
            visible.c.publication_id,
            waiting.label("waiting"),
            (participant.c.status != "pending").label("responded"),
            deadline.label("deadline"),
        )
        .select_from(visible)
        .join(
            work,
            and_(
                work.c.id == visible.c.request_id,
                work.c.project_id == visible.c.project_id,
            ),
        )
        .join(
            participant,
            and_(
                participant.c.request_id == work.c.id, participant.c.user_id == user_id
            ),
        )
        .where(
            visible.c.publication_id.in_(publication_ids),
            work.c.status != "cancelled",
            or_(
                work.c.status != "requested",
                deadline.is_(None),
                deadline > func.clock_timestamp(),
            ),
        )
    )
    fixed = GuestFixedApprovalRepository()
    request = fixed.requests
    bindings = fixed.bindings
    approvals = (
        fixed.visible(user_id)
        .where(
            bindings.c.publication_id.in_(publication_ids),
            request.c.status != "cancelled",
            func.guest_approval_has_access(request.c.id, user_id),
        )
        .with_only_columns(
            bindings.c.publication_id,
            (request.c.status == "requested").label("waiting"),
            (request.c.status != "requested").label("responded"),
            fixed.deadline().label("deadline"),
        )
    )
    own = union_all(workflow, approvals).subquery()
    grouped = db.execute(
        select(
            own.c.publication_id,
            func.bool_or(own.c.waiting).label("waiting"),
            func.bool_or(own.c.responded).label("responded"),
            func.min(own.c.deadline).filter(own.c.waiting).label("refresh_at"),
        ).group_by(own.c.publication_id)
    ).mappings()
    return {row["publication_id"]: row for row in grouped}
