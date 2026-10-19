"""既存認証を保ち、新しい公開操作だけをREAD COMMITTEDで再検証する。"""

from contextlib import contextmanager
from functools import wraps

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, ForbiddenError, TokenExpiredError
from app.core.mfa import require_verified
from app.db.base import Base
from app.models.project import Project
from app.models.review_account import ReviewAccountGrant
from app.models.session import UserSession
from app.models.user import User
from app.services.authorization import AuthorizationService

PUBLICATION_ACCESS_ENABLED = True


def retry_publication_operation(method):
    """新しい操作だけを最大3回再試行し、既存APIの失敗方針を変えない。"""

    @wraps(method)
    def wrapped(*args, **kwargs):
        for attempt in range(3):
            try:
                return method(*args, **kwargs)
            except DBAPIError as error:
                code = getattr(error.orig, "pgcode", None)
                if code in {"40001", "40P01"} and attempt < 2:
                    continue
                if code in {"40001", "40P01", "23514", "23505"}:
                    raise ConflictError() from None
                raise

    return wrapped


class PublicationAccessContext:
    """本人資格の調整→Project境界→新statementでの再認可を共通化する。"""

    def __init__(self):
        """既存のProject権限判定を再利用する。"""
        self.authorization = AuthorizationService()

    def verify(self, db, original, user, *, internal):
        """最初のdependency後に変化した本人・session・MFAも再確認する。"""
        actor = db.get(User, user.id, populate_existing=True)
        if (
            actor is None
            or not actor.is_active
            or actor.deleted_at is not None
            or actor.password_change_required
        ):
            raise ForbiddenError()
        if internal and actor.user_type != "internal":
            raise ForbiddenError()
        active = original.info.get("auth_session")
        if active is None:
            raise TokenExpiredError()
        current = db.get(UserSession, active.id, populate_existing=True)
        now = db.scalar(select(func.clock_timestamp()))
        if (
            current is None
            or current.user_id != actor.id
            or current.revoked_at is not None
            or current.expires_at <= now
            or current.absolute_expires_at <= now
        ):
            raise TokenExpiredError()
        require_verified(actor, current)
        if db.get(ReviewAccountGrant, actor.id) is not None:
            raise ForbiddenError()
        db.info["auth_session"] = current
        return actor

    def permission(self, db, actor, project_id, permission):
        """現在のProject所属とpermissionを同時に要求する。"""
        if not self.authorization.has_project_permission(
            db, user=actor, project_id=project_id, permission_code=permission
        ):
            raise ForbiddenError()

    def management(self, db, actor, project_id, permission):
        """内部管理のメタデータ権限を本文閲覧へ流用しない。"""
        p, m, r, t = (
            Base.metadata.tables[n]
            for n in ("projects", "tenant_members", "roles", "tenants")
        )
        tenant_id = db.info["tenant_id"]
        valid_project = db.scalar(
            select(p.c.id)
            .join(t, t.c.id == p.c.tenant_id)
            .where(
                p.c.id == project_id,
                p.c.tenant_id == tenant_id,
                p.c.deleted_at.is_(None),
                t.c.status == "active",
            )
        )
        if valid_project is None:
            raise ForbiddenError()
        admin = db.scalar(
            select(m.c.id)
            .join(r, r.c.id == m.c.role_id)
            .where(
                m.c.tenant_id == tenant_id,
                m.c.user_id == actor.id,
                m.c.status == "active",
                r.c.scope == "tenant",
                r.c.key.in_(["tenant_owner", "tenant_admin"]),
            )
        )
        if admin is None:
            self.permission(db, actor, project_id, permission)

    @contextmanager
    def transaction(
        self,
        original,
        user,
        project_id=None,
        *,
        write=False,
        qualifying=False,
        management=None,
        download=False,
    ):
        """共有閲覧には組織選択を要求せず、内部contextをそのまま開放しない。"""
        if (
            original.info.get("demo_id") is not None
            or original.info.get("data_realm") == "demo"
            or original.info.get("mcp_connection_id") is not None
        ):
            raise ForbiddenError()
        internal = project_id is not None
        if not internal and not PUBLICATION_ACCESS_ENABLED:
            raise ForbiddenError()
        if internal and (
            original.info.get("tenant_id") is None
            or original.info.get("tenant_user_id") != user.id
        ):
            raise ForbiddenError()
        info = {
            k: original.info[k]
            for k in ("tenant_id", "tenant_user_id", "data_realm")
            if internal and k in original.info
        }
        with (
            original.get_bind()
            .engine.connect()
            .execution_options(isolation_level="READ COMMITTED") as connection,
            Session(bind=connection, info=info) as db,
            db.begin(),
        ):
            if write or download:
                # 既存APIが先に取るFOR UPDATEと新しいactor/Project FKを逆順にしない。
                db.execute(
                    select(User.id)
                    .where(User.id == user.id)
                    .with_for_update(read=True, key_share=True)
                )
                if project_id is not None:
                    db.execute(
                        select(Project.id)
                        .where(Project.id == project_id)
                        .with_for_update(read=True, key_share=True)
                    )
            lock = (
                "pg_advisory_xact_lock"
                if qualifying
                else "pg_advisory_xact_lock_shared"
            )
            db.execute(text(f"SELECT {lock}(1398362932,0)"))
            if internal:
                db.execute(
                    text("SELECT publication_lock(:project,:reading)"),
                    {"project": project_id, "reading": not write},
                )
            actor = self.verify(db, original, user, internal=internal)
            if internal:
                if management:
                    self.management(db, actor, project_id, management)
                else:
                    self.permission(db, actor, project_id, "artifact_publication:read")
                    if write:
                        self.permission(
                            db, actor, project_id, "artifact_publication:publish"
                        )
            yield db, actor
