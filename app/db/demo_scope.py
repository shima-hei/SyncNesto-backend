"""検証済みデモContextのIdentity境界と、commit前の寿命・容量検証。"""

from datetime import UTC, datetime

from sqlalchemy import event, func, inspect, select
from sqlalchemy.orm import Session, with_loader_criteria

from app.core.exceptions import DemoLimitError, ForbiddenError, TokenExpiredError
from app.models.demo import DemoOwnedUser, DemoSession
from app.models.project import Project
from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.session import UserSession
from app.models.tenant import Tenant, TenantMember
from app.models.user import User
from app.repositories.demo import BUSINESS_TABLES, DemoRepository


def lock_active_demo(db: Session) -> DemoSession:
    """各commit後にもロックを取り直し、終了後の書込を防ぐ。"""
    # populate_existingは延長中の値も上書きするため、DBの状態だけをCoreで検証する。
    table = DemoSession.__table__
    state = (
        db.connection()
        .execute(
            select(
                table.c.status, table.c.expires_at, UserSession.__table__.c.revoked_at
            )
            .join(
                UserSession.__table__, UserSession.__table__.c.id == table.c.session_id
            )
            .where(table.c.id == db.info["demo_id"])
            .with_for_update(of=table)
        )
        .first()
    )
    if (
        state is None
        or state.status != "active"
        or state.revoked_at is not None
        or state.expires_at <= datetime.now(UTC)
    ):
        raise TokenExpiredError()
    demo = db.get(DemoSession, db.info["demo_id"])
    assert demo is not None
    return demo


@event.listens_for(Session, "do_orm_execute")
def scope_demo_identities(state) -> None:
    """業務表に加え、共通Identityと組織・所属の検索もデモの所有範囲に絞る。"""
    demo_id = state.session.info.get("demo_id")
    if demo_id is None:
        return
    if state.is_update or state.is_delete or state.is_insert:
        lock_active_demo(state.session)
    if not (state.is_select or state.is_update or state.is_delete):
        return
    owned = select(DemoOwnedUser.user_id).where(DemoOwnedUser.demo_id == demo_id)
    tenant_id = state.session.info["tenant_id"]
    options = [
        with_loader_criteria(User, User.id.in_(owned), include_aliases=True),
        with_loader_criteria(Tenant, Tenant.id == tenant_id, include_aliases=True),
        with_loader_criteria(
            TenantMember, TenantMember.tenant_id == tenant_id, include_aliases=True
        ),
        with_loader_criteria(
            UserRole, UserRole.user_id.in_(owned), include_aliases=True
        ),
        with_loader_criteria(
            UserSession, UserSession.user_id.in_(owned), include_aliases=True
        ),
    ]
    state.statement = state.statement.options(*options)


@event.listens_for(Session, "before_flush")
def validate_demo_writes(db: Session, flush_context, instances) -> None:
    """同時更新を直列化し、共通Roleや他のIdentityを変更させない。"""
    if db.info.get("demo_id") is None:
        return
    demo = lock_active_demo(db)
    assert demo.tenant_id is not None
    owned = set(DemoRepository().owned_ids(db, demo.id))
    owned.update(
        row.user_id
        for row in db.new
        if isinstance(row, DemoOwnedUser) and row.demo_id == demo.id
    )
    for row in db.new | db.dirty | db.deleted:
        if isinstance(row, (Role, Permission, RolePermission, UserRole)):
            raise ForbiddenError()
        if isinstance(row, User):
            if row in db.new:
                if not row.email.endswith(f"-{demo.id.hex}@demo.syncnesto.example.com"):
                    raise ForbiddenError()
            else:
                if row.id not in owned:
                    raise ForbiddenError()
                allowed = {"name", "avatar_key", "version", "updated_by", "updated_at"}
                if any(
                    attr.history.has_changes() and attr.key not in allowed
                    for attr in inspect(row).attrs
                ):
                    raise ForbiddenError()
        if isinstance(row, Tenant):
            if row.id != demo.tenant_id or row in db.new or row in db.deleted:
                raise ForbiddenError()
            if (
                inspect(row).attrs.slug.history.has_changes()
                or inspect(row).attrs.status.history.has_changes()
            ):
                raise ForbiddenError()
        if isinstance(row, TenantMember):
            if row.tenant_id != demo.tenant_id or row.user_id not in owned:
                raise ForbiddenError()
        if isinstance(row, DemoOwnedUser) and row.demo_id != demo.id:
            raise ForbiddenError()
    new_users = sum(isinstance(row, User) for row in db.new)
    if len(owned) + new_users > 10:
        raise DemoLimitError()
    new_projects = sum(isinstance(row, Project) for row in db.new)
    if (
        new_projects
        and (
            db.connection().scalar(
                select(func.count())
                .select_from(Project)
                .where(Project.tenant_id == demo.tenant_id)
            )
            or 0
        )
        + new_projects
        > 3
    ):
        raise DemoLimitError()
    added = sum(row.__table__.name in BUSINESS_TABLES for row in db.new)
    if added and DemoRepository().business_count(db, demo.tenant_id) + added > 500:
        raise DemoLimitError()
