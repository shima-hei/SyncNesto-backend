"""Identityを共有し、所属と組織内プロフィールを独立して管理する。"""

import secrets
from datetime import UTC, datetime
from typing import cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import (
    ConflictError,
    DuplicateResourceError,
    ForbiddenError,
    NotFoundError,
    VersionConflictError,
)
from app.core.security import get_password_hash
from app.models.project import Project, ProjectMember
from app.models.rbac import Role
from app.models.tenant import Tenant, TenantMember
from app.models.user import User
from app.repositories.tenant import TenantRepository
from app.schemas.tenant import (
    TenantChoice,
    TenantCreate,
    TenantMemberAdd,
    TenantMemberRead,
    TenantMemberUpdate,
    TenantRead,
    TenantRoleKey,
    TenantUpdate,
    TenantUserCreate,
    TenantUserCreated,
)
from app.services.audit_log import AuditLogService
from app.services.onboarding import mark_initial_password


class TenantService:
    """組織変更の認可、Owner保護、原子的な所属更新。"""

    def __init__(self) -> None:
        """既存のRepository・監査ヘルパーを利用する。"""
        self.repository = TenantRepository()
        self.audit = AuditLogService()

    def choices(self, db: Session, user_id: int) -> list[TenantChoice]:
        """本人が選択できる組織を返す。"""
        return [
            TenantChoice(
                **TenantRead.model_validate(tenant).model_dump(), role_key=role.key
            )
            for tenant, role in self.repository.choices(db, user_id)
        ]

    def _read_member(self, db: Session, member: TenantMember) -> TenantMemberRead:
        """組織内プロフィールを返し、Identityの管理情報を公開しない。"""
        user = db.get(User, member.user_id)
        role = db.get(Role, member.role_id)
        assert user is not None and role is not None
        return TenantMemberRead(
            id=member.id,
            user_id=user.id,
            email=user.email,
            display_name=member.display_name or user.name,
            department=member.department,
            position=member.position,
            role_key=cast(TenantRoleKey, role.key),
            status=member.status,
            version=member.version,
            joined_at=member.joined_at,
        )

    def members(self, db: Session, tenant_id: int) -> list[TenantMemberRead]:
        """所属一覧を現在の組織に限定する。"""
        return [
            self._read_member(db, member)
            for member, _, _ in self.repository.members(db, tenant_id)
        ]

    def _role(self, db: Session, key: str) -> Role:
        """Ownerの委譲はOwnerだけに許可する。"""
        if key == "tenant_owner" and db.info.get("tenant_role") != "tenant_owner":
            raise ForbiddenError()
        role = self.repository.role(db, key)
        if role is None:
            raise NotFoundError()
        return role

    def _commit(self, db: Session) -> None:
        """競合した一意制約を業務例外へ変換する。"""
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise DuplicateResourceError() from exc

    def _audit(
        self,
        db: Session,
        actor: int,
        event: str,
        target: int | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """認証情報を含めずに組織操作を記録する。"""
        self.audit.record(
            db,
            event_type=event,
            actor_user_id=actor,
            target_user_id=target,
            resource_type="tenant",
            resource_id=db.info.get("tenant_id"),
            metadata=metadata,
        )

    def create(self, db: Session, data: TenantCreate, actor: int) -> Tenant:
        """運営者が既存Identityを唯一の初期Ownerとして組織を作る。"""
        owner = self.repository.identity(db, str(data.owner_email))
        if owner is None:
            raise NotFoundError(error_messages.TENANT_IDENTITY_NOT_FOUND)
        role = self.repository.role(db, "tenant_owner")
        assert role is not None
        tenant = Tenant(name=data.name, slug=data.slug)
        db.add(tenant)
        try:
            db.flush()
            db.add(
                TenantMember(
                    tenant_id=tenant.id,
                    user_id=owner.id,
                    role_id=role.id,
                    display_name=owner.name,
                )
            )
            self._commit(db)
        except IntegrityError as exc:
            db.rollback()
            raise DuplicateResourceError() from exc
        db.refresh(tenant)
        self.audit.record(
            db,
            event_type="tenant.created",
            tenant_id=tenant.id,
            actor_user_id=actor,
            target_user_id=owner.id,
            resource_type="tenant",
            resource_id=tenant.id,
        )
        return tenant

    def update(
        self, db: Session, tenant_id: int, data: TenantUpdate, actor: int
    ) -> Tenant:
        """組織設定を排他制御付きで更新する。"""
        tenant = self.repository.lock(db, tenant_id)
        assert tenant is not None
        if tenant.version != data.version:
            raise VersionConflictError(TenantRead.model_validate(tenant).model_dump())
        tenant.name = data.name
        tenant.version += 1
        self._commit(db)
        self._audit(db, actor, "tenant.updated")
        return tenant

    def add_member(
        self, db: Session, tenant_id: int, data: TenantMemberAdd, actor: int
    ) -> TenantMemberRead:
        """有効なIdentityを完全一致で追加し、古いProject権限は復活させない。"""
        self.repository.lock(db, tenant_id)
        user = self.repository.identity(db, str(data.email))
        if user is None:
            raise NotFoundError(error_messages.TENANT_IDENTITY_NOT_FOUND)
        role = self._role(db, data.role_key)
        member = self.repository.member(db, tenant_id, user.id)
        if member is not None and member.status != "removed":
            raise DuplicateResourceError()
        if member is None:
            member = TenantMember(
                tenant_id=tenant_id,
                user_id=user.id,
                role_id=role.id,
                display_name=user.name,
            )
            db.add(member)
        else:
            member.role_id = role.id
            member.status = "active"
            member.version += 1
        self._commit(db)
        result = self._read_member(db, member)
        self._audit(db, actor, "tenant.member_added", user.id)
        return result

    def change_member(
        self,
        db: Session,
        tenant_id: int,
        user_id: int,
        data: TenantMemberUpdate,
        actor: int,
        *,
        remove: bool = False,
    ) -> TenantMemberRead:
        """組織内だけの更新・停止・削除を行い、最後の有効Ownerを保護する。"""
        self.repository.lock(db, tenant_id)
        member = self.repository.member(db, tenant_id, user_id)
        if member is None or member.status == "removed":
            raise NotFoundError()
        current = self._read_member(db, member)
        if current.version != data.version:
            raise VersionConflictError(current.model_dump(mode="json"))
        if (
            current.role_key == "tenant_owner"
            and db.info.get("tenant_role") != "tenant_owner"
        ):
            raise ForbiddenError()
        changes = data.model_dump(exclude_unset=True, exclude={"version", "role_key"})
        if remove:
            changes["status"] = "removed"
        if changes.get("status", member.status) is None:
            raise ConflictError(error_messages.TENANT_STATUS_REQUIRED)
        role = self._role(db, data.role_key) if data.role_key is not None else None
        new_role = data.role_key or current.role_key
        new_status = changes.get("status", member.status)
        if (
            current.role_key == "tenant_owner"
            and current.status == "active"
            and (new_role != "tenant_owner" or new_status != "active")
            and self.repository.owner_count(db, tenant_id) <= 1
        ):
            db.rollback()
            raise ConflictError(error_messages.LAST_TENANT_OWNER_REQUIRED)
        if role is not None:
            member.role_id = role.id
        for key, value in changes.items():
            if key == "display_name" and value is None:
                raise ConflictError(error_messages.TENANT_DISPLAY_NAME_REQUIRED)
            setattr(member, key, value)
        member.version += 1
        if remove:
            project_ids = db.query(Project.id).filter(Project.tenant_id == tenant_id)
            db.query(ProjectMember).filter(
                ProjectMember.project_id.in_(project_ids),
                ProjectMember.user_id == user_id,
                ProjectMember.deleted_at.is_(None),
            ).update(
                {
                    ProjectMember.deleted_at: datetime.now(UTC),
                    ProjectMember.version: ProjectMember.version + 1,
                },
                synchronize_session=False,
            )
        self._commit(db)
        result = self._read_member(db, member)
        self._audit(
            db,
            actor,
            "tenant.member_removed" if remove else "tenant.member_updated",
            user_id,
            metadata={
                "role_before": current.role_key,
                "role_after": result.role_key,
                "status_before": current.status,
                "status_after": result.status,
            },
        )
        return result

    def create_user(
        self, db: Session, tenant_id: int, data: TenantUserCreate, actor: int
    ) -> TenantUserCreated:
        """新規Identityと所属を一度に作り、初期パスワードを一度だけ返す。"""
        self.repository.lock(db, tenant_id)
        role = self._role(db, data.role_key)
        password = secrets.token_urlsafe(24)
        user = User(
            email=str(data.email),
            name=data.display_name,
            hashed_password=get_password_hash(password),
            created_by=actor,
        )
        mark_initial_password(user)
        db.add(user)
        try:
            db.flush()
            member = TenantMember(
                tenant_id=tenant_id,
                user_id=user.id,
                role_id=role.id,
                display_name=data.display_name,
                department=data.department,
                position=data.position,
            )
            db.add(member)
            self._commit(db)
        except IntegrityError as exc:
            db.rollback()
            raise DuplicateResourceError() from exc
        result = TenantUserCreated(
            member=self._read_member(db, member), initial_password=password
        )
        self._audit(db, actor, "tenant.user_created", user.id)
        return result
