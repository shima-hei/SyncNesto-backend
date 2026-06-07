"""プロジェクト関連のサービス層を定義するモジュール。"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import (
    DuplicateResourceError,
    ForbiddenError,
    NotFoundError,
)
from app.models.project import Project, ProjectMember
from app.models.rbac import Role
from app.models.user import User
from app.repositories.project import ProjectMemberRepository, ProjectRepository
from app.repositories.rbac import RbacRepository
from app.repositories.user import UserRepository
from app.schemas.project import (
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberUpdate,
    ProjectRead,
    ProjectUpdate,
)
from app.services.audit_log import AuditEventType, AuditLogService
from app.services.authorization import AuthorizationService
from app.services.conflict import (
    raise_duplicate_after_rollback,
    raise_if_version_conflict,
)
from app.services.session import SessionService


class ProjectService:
    """プロジェクトに関するビジネスロジックを提供する。"""

    def __init__(
        self,
        repository: ProjectRepository | None = None,
        authorization_service: AuthorizationService | None = None,
        audit_log_service: AuditLogService | None = None,
    ) -> None:
        """ProjectServiceを初期化する。

        Args:
            repository: プロジェクトRepository。
            authorization_service: 認可サービス。
            audit_log_service: 監査ログサービス。
        """
        self.repository = repository or ProjectRepository()
        self.authorization_service = authorization_service or AuthorizationService()
        self.audit_log_service = audit_log_service or AuditLogService()

    def create_project(
        self,
        db: Session,
        project_in: ProjectCreate,
        actor_id: int | None = None,
    ) -> Project:
        """プロジェクトを作成する。

        Args:
            db: DBセッション。
            project_in: プロジェクト作成リクエストの入力値。
            actor_id: 作成者ユーザーID。

        Returns:
            作成されたプロジェクト。
        """
        if self.repository.get_by_project_code(db, project_in.project_code) is not None:
            raise DuplicateResourceError(error_messages.PROJECT_CODE_ALREADY_EXISTS)

        try:
            project = self.repository.create(db, project_in, actor_id=actor_id)
            self.audit_log_service.record(
                db,
                event_type=AuditEventType.PROJECT_CREATED,
                actor_user_id=actor_id,
                project_id=project.id,
                resource_type="project",
                resource_id=project.id,
                metadata={"project_code": project.project_code, "name": project.name},
            )
            return project
        except IntegrityError as exc:
            raise_duplicate_after_rollback(
                db,
                error_messages.PROJECT_CODE_ALREADY_EXISTS,
                exc,
            )

    def list_projects(self, db: Session, current_user: User) -> list[Project]:
        """ユーザーが閲覧可能なプロジェクト一覧を取得する。

        Args:
            db: DBセッション。
            current_user: 認証済みユーザー。

        Returns:
            閲覧可能なプロジェクト一覧。
        """
        if self.authorization_service.has_system_permission(
            db,
            user=current_user,
            permission_code="project:read",
        ):
            return self.repository.list(db)

        return self.repository.list_by_user(db, current_user.id)

    def list_projects_paginated(
        self,
        db: Session,
        current_user: User,
        *,
        page: int,
        page_size: int,
        q: str | None = None,
        status: str | None = None,
    ) -> tuple[list[Project], int]:
        """ユーザーが閲覧可能なプロジェクト一覧をページング付きで取得する。

        Args:
            db: DBセッション。
            current_user: 認証済みユーザー。
            page: ページ番号。
            page_size: 1ページあたりの件数。
            q: 検索キーワード。
            status: ステータス絞り込み。

        Returns:
            閲覧可能なプロジェクト一覧と総件数。
        """
        if self.authorization_service.has_system_permission(
            db,
            user=current_user,
            permission_code="project:read",
        ):
            return self.repository.list_paginated(
                db,
                page=page,
                page_size=page_size,
                q=q,
                status=status,
            )

        return self.repository.list_by_user_paginated(
            db,
            user_id=current_user.id,
            page=page,
            page_size=page_size,
            q=q,
            status=status,
        )

    def get_project(self, db: Session, project_id: int) -> Project:
        """プロジェクトを取得する。

        Args:
            db: DBセッション。
            project_id: 取得対象プロジェクトID。

        Returns:
            取得されたプロジェクト。

        Raises:
            NotFoundError: プロジェクトが存在しない場合。
        """
        project = self.repository.get_by_id(db, project_id)
        if project is None:
            raise NotFoundError(error_messages.PROJECT_NOT_FOUND)

        return project

    def update_project(
        self,
        db: Session,
        project_id: int,
        project_in: ProjectUpdate,
        actor_id: int | None = None,
    ) -> Project:
        """プロジェクトを更新する。

        Args:
            db: DBセッション。
            project_id: 更新対象プロジェクトID。
            project_in: プロジェクト更新リクエストの入力値。
            actor_id: 更新者ユーザーID。

        Returns:
            更新されたプロジェクト。

        Raises:
            VersionConflictError: リクエストのversionが最新ではない場合。
        """
        project = self.get_project(db, project_id)
        raise_if_version_conflict(
            current_version=project.version,
            requested_version=project_in.version,
            current=ProjectRead.model_validate(project).model_dump(),
        )

        if (
            project_in.project_code is not None
            and project_in.project_code != project.project_code
            and self.repository.get_by_project_code(db, project_in.project_code)
            is not None
        ):
            raise DuplicateResourceError(error_messages.PROJECT_CODE_ALREADY_EXISTS)

        try:
            project = self.repository.update(
                db,
                project=project,
                project_in=project_in,
                actor_id=actor_id,
            )
            self.audit_log_service.record(
                db,
                event_type=AuditEventType.PROJECT_UPDATED,
                actor_user_id=actor_id,
                project_id=project.id,
                resource_type="project",
                resource_id=project.id,
                metadata={"updated_fields": sorted(project_in.model_fields_set)},
            )
            return project
        except IntegrityError as exc:
            raise_duplicate_after_rollback(
                db,
                error_messages.PROJECT_CODE_ALREADY_EXISTS,
                exc,
            )

    def delete_project(
        self,
        db: Session,
        project_id: int,
        actor_id: int | None = None,
    ) -> None:
        """プロジェクトを論理削除する。

        Args:
            db: DBセッション。
            project_id: 削除対象プロジェクトID。
            actor_id: 更新者ユーザーID。
        """
        project = self.get_project(db, project_id)
        self.repository.soft_delete(db, project, actor_id=actor_id)
        self.audit_log_service.record(
            db,
            event_type=AuditEventType.PROJECT_DELETED,
            actor_user_id=actor_id,
            project_id=project.id,
            resource_type="project",
            resource_id=project.id,
            metadata={"project_code": project.project_code, "name": project.name},
        )

    def get_current_project_role(
        self,
        db: Session,
        *,
        project_id: int,
        current_user: User,
    ) -> tuple[Role | None, bool]:
        """現在のユーザーの対象プロジェクト内ロールを取得する。

        Args:
            db: DBセッション。
            project_id: 対象プロジェクトID。
            current_user: 認証済みユーザー。

        Returns:
            プロジェクトロールとsystem_admin判定。

        Raises:
            ForbiddenError: system_adminでもプロジェクトメンバーでもない場合。
            NotFoundError: プロジェクトが存在しない場合。
        """
        self.get_project(db, project_id)
        is_system_admin = self.authorization_service.has_system_permission(
            db,
            user=current_user,
            permission_code="project:read",
        )
        role = ProjectMemberRepository().get_role_by_project_user(
            db,
            project_id=project_id,
            user_id=current_user.id,
        )
        if role is None and not is_system_admin:
            raise ForbiddenError()

        return role, is_system_admin


class ProjectMemberService:
    """プロジェクトメンバーのビジネスロジックを提供する。"""

    def __init__(
        self,
        repository: ProjectMemberRepository | None = None,
        project_repository: ProjectRepository | None = None,
        rbac_repository: RbacRepository | None = None,
        user_repository: UserRepository | None = None,
        session_service: SessionService | None = None,
        audit_log_service: AuditLogService | None = None,
    ) -> None:
        """ProjectMemberServiceを初期化する。

        Args:
            repository: プロジェクトメンバーRepository。
            project_repository: プロジェクトRepository。
            rbac_repository: RBAC Repository。
            user_repository: ユーザーRepository。
            session_service: 認証セッションサービス。
            audit_log_service: 監査ログサービス。
        """
        self.repository = repository or ProjectMemberRepository()
        self.project_repository = project_repository or ProjectRepository()
        self.rbac_repository = rbac_repository or RbacRepository()
        self.user_repository = user_repository or UserRepository()
        self.session_service = session_service or SessionService()
        self.audit_log_service = audit_log_service or AuditLogService()

    def list_members(self, db: Session, project_id: int) -> list[ProjectMember]:
        """プロジェクトメンバー一覧を取得する。

        Args:
            db: DBセッション。
            project_id: プロジェクトID。

        Returns:
            プロジェクトメンバー一覧。
        """
        self._ensure_project_exists(db, project_id)
        return self.repository.list_by_project(db, project_id)

    def get_member_role(self, db: Session, member: ProjectMember) -> Role:
        """プロジェクトメンバーに紐づくロールを取得する。

        Args:
            db: DBセッション。
            member: プロジェクトメンバー。

        Returns:
            プロジェクトロール。

        Raises:
            NotFoundError: 紐づくロールが存在しない場合。
        """
        role = self.rbac_repository.get_role_by_id(db, member.role_id)
        if role is None:
            raise NotFoundError(error_messages.PROJECT_ROLE_NOT_FOUND)

        return role

    def list_member_roles_by_role_id(
        self,
        db: Session,
        members: list[ProjectMember],
    ) -> dict[int, Role]:
        """プロジェクトメンバー一覧に必要なロールを取得する。

        Args:
            db: DBセッション。
            members: プロジェクトメンバー一覧。

        Returns:
            role_idをkeyにしたロール辞書。
        """
        roles: dict[int, Role] = {}
        for member in members:
            if member.role_id not in roles:
                roles[member.role_id] = self.get_member_role(db, member)

        return roles

    def list_member_users(
        self,
        db: Session,
        *,
        project_id: int,
        q: str | None = None,
        limit: int = 20,
    ) -> list[User]:
        """プロジェクト所属ユーザー一覧を取得する。

        Args:
            db: DBセッション。
            project_id: プロジェクトID。
            q: 検索キーワード。
            limit: 最大取得件数。

        Returns:
            プロジェクト所属ユーザー一覧。
        """
        self._ensure_project_exists(db, project_id)
        return self.repository.list_users_by_project(
            db,
            project_id=project_id,
            q=q,
            limit=limit,
        )

    def add_member(
        self,
        db: Session,
        *,
        project_id: int,
        member_in: ProjectMemberCreate,
        actor_id: int | None = None,
    ) -> ProjectMember:
        """プロジェクトメンバーを追加する。

        Args:
            db: DBセッション。
            project_id: プロジェクトID。
            member_in: メンバー追加リクエストの入力値。
            actor_id: 操作ユーザーID。

        Returns:
            追加されたプロジェクトメンバー。

        Raises:
            DuplicateResourceError: 既に所属している場合。
        """
        self._ensure_project_exists(db, project_id)
        self._ensure_user_exists(db, member_in.user_id)
        role = self._get_project_role_by_key(db, member_in.role_key)
        existing_member = self.repository.get_by_project_user(
            db,
            project_id=project_id,
            user_id=member_in.user_id,
        )
        if existing_member is not None:
            raise DuplicateResourceError(error_messages.PROJECT_MEMBER_ALREADY_EXISTS)

        try:
            member = self.repository.create(
                db,
                project_id=project_id,
                user_id=member_in.user_id,
                role_id=role.id,
            )
            self.audit_log_service.record(
                db,
                event_type=AuditEventType.PROJECT_MEMBER_ADDED,
                actor_user_id=actor_id,
                target_user_id=member.user_id,
                project_id=project_id,
                resource_type="project_member",
                resource_id=member.id,
                metadata={"role_key": role.key},
            )
            self.session_service.revoke_user_sessions(
                db,
                user_id=member.user_id,
                actor_user_id=actor_id,
                project_id=project_id,
            )
            return member
        except IntegrityError as exc:
            raise_duplicate_after_rollback(
                db,
                error_messages.PROJECT_MEMBER_ALREADY_EXISTS,
                exc,
            )

    def update_member(
        self,
        db: Session,
        *,
        project_id: int,
        user_id: int,
        member_in: ProjectMemberUpdate,
        actor_id: int | None = None,
    ) -> ProjectMember:
        """プロジェクトメンバーのロールを更新する。

        Args:
            db: DBセッション。
            project_id: プロジェクトID。
            user_id: 更新対象ユーザーID。
            member_in: メンバー更新リクエストの入力値。
            actor_id: 操作ユーザーID。

        Returns:
            更新されたプロジェクトメンバー。

        Raises:
            VersionConflictError: リクエストのversionが最新ではない場合。
        """
        member = self._get_member(db, project_id=project_id, user_id=user_id)
        raise_if_version_conflict(
            current_version=member.version,
            requested_version=member_in.version,
            current=self._build_member_current(db, member),
        )

        before_role = self.get_member_role(db, member)
        role = self._get_project_role_by_key(db, member_in.role_key)
        updated_member = self.repository.update_role(db, member=member, role_id=role.id)
        self.audit_log_service.record(
            db,
            event_type=AuditEventType.PROJECT_MEMBER_ROLE_CHANGED,
            actor_user_id=actor_id,
            target_user_id=updated_member.user_id,
            project_id=project_id,
            resource_type="project_member",
            resource_id=updated_member.id,
            metadata={
                "before_role_key": before_role.key,
                "after_role_key": role.key,
            },
        )
        self.session_service.revoke_user_sessions(
            db,
            user_id=updated_member.user_id,
            actor_user_id=actor_id,
            project_id=project_id,
        )
        return updated_member

    def remove_member(
        self,
        db: Session,
        *,
        project_id: int,
        user_id: int,
        actor_id: int | None = None,
    ) -> None:
        """プロジェクトメンバーを物理削除する。

        Args:
            db: DBセッション。
            project_id: プロジェクトID。
            user_id: 削除対象ユーザーID。
            actor_id: 操作ユーザーID。
        """
        member = self._get_member(db, project_id=project_id, user_id=user_id)
        role = self.get_member_role(db, member)
        member_id = member.id
        self.repository.delete(db, member)
        self.audit_log_service.record(
            db,
            event_type=AuditEventType.PROJECT_MEMBER_REMOVED,
            actor_user_id=actor_id,
            target_user_id=user_id,
            project_id=project_id,
            resource_type="project_member",
            resource_id=member_id,
            metadata={"role_key": role.key},
        )
        self.session_service.revoke_user_sessions(
            db,
            user_id=user_id,
            actor_user_id=actor_id,
            project_id=project_id,
        )

    def _ensure_project_exists(self, db: Session, project_id: int) -> None:
        """プロジェクトが存在することを確認する。"""
        if self.project_repository.get_by_id(db, project_id) is None:
            raise NotFoundError(error_messages.PROJECT_NOT_FOUND)

    def _ensure_user_exists(self, db: Session, user_id: int) -> None:
        """ユーザーが存在することを確認する。"""
        if self.user_repository.get_by_id(db, user_id) is None:
            raise NotFoundError(error_messages.USER_NOT_FOUND)

    def _get_project_role_by_key(self, db: Session, role_key: str):
        """project scopeのロールkeyからロールを取得する。

        Args:
            db: DBセッション。
            role_key: プロジェクトロールkey。

        Returns:
            プロジェクトロール。

        Raises:
            NotFoundError: プロジェクトロールが存在しない場合。
        """
        role = self.rbac_repository.get_role_by_key_scope(
            db,
            key=role_key,
            scope="project",
        )
        if role is None:
            raise NotFoundError(error_messages.PROJECT_ROLE_NOT_FOUND)

        return role

    def _build_member_current(
        self,
        db: Session,
        member: ProjectMember,
    ) -> dict[str, object]:
        """バージョン競合時に返す最新メンバー情報を組み立てる。"""
        role = self.rbac_repository.get_role_by_id(db, member.role_id)
        if role is None:
            raise NotFoundError(error_messages.PROJECT_ROLE_NOT_FOUND)

        return {
            "id": member.id,
            "project_id": member.project_id,
            "user_id": member.user_id,
            "role": {
                "key": role.key,
                "name": role.name,
            },
            "version": member.version,
        }

    def _get_member(
        self,
        db: Session,
        *,
        project_id: int,
        user_id: int,
    ) -> ProjectMember:
        """プロジェクトメンバーを取得する。"""
        self._ensure_project_exists(db, project_id)
        member = self.repository.get_by_project_user(
            db,
            project_id=project_id,
            user_id=user_id,
        )
        if member is None:
            raise NotFoundError(error_messages.PROJECT_MEMBER_NOT_FOUND)

        return member
