"""プロジェクト関連のサービス層を定義するモジュール。"""

from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError, VersionConflictError
from app.models.project import Project, ProjectMember
from app.models.user import User
from app.repositories.project import ProjectMemberRepository, ProjectRepository
from app.repositories.rbac import RbacRepository
from app.repositories.user import UserRepository
from app.schemas.project import (
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberRead,
    ProjectMemberUpdate,
    ProjectRead,
    ProjectUpdate,
)
from app.services.authorization import AuthorizationService


class ProjectService:
    """プロジェクトに関するビジネスロジックを提供する。"""

    def __init__(
        self,
        repository: ProjectRepository | None = None,
        authorization_service: AuthorizationService | None = None,
    ) -> None:
        """ProjectServiceを初期化する。

        Args:
            repository: プロジェクトRepository。
            authorization_service: 認可サービス。
        """
        self.repository = repository or ProjectRepository()
        self.authorization_service = authorization_service or AuthorizationService()

    def create_project(self, db: Session, project_in: ProjectCreate) -> Project:
        """プロジェクトを作成する。

        Args:
            db: DBセッション。
            project_in: プロジェクト作成リクエストの入力値。

        Returns:
            作成されたプロジェクト。
        """
        return self.repository.create(db, project_in)

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
            raise NotFoundError("Project not found")

        return project

    def update_project(
        self,
        db: Session,
        project_id: int,
        project_in: ProjectUpdate,
    ) -> Project:
        """プロジェクトを更新する。

        Args:
            db: DBセッション。
            project_id: 更新対象プロジェクトID。
            project_in: プロジェクト更新リクエストの入力値。

        Returns:
            更新されたプロジェクト。

        Raises:
            VersionConflictError: リクエストのversionが最新ではない場合。
        """
        project = self.get_project(db, project_id)
        if project.version != project_in.version:
            current = ProjectRead.model_validate(project).model_dump()
            raise VersionConflictError(current=current)

        return self.repository.update(db, project=project, project_in=project_in)

    def delete_project(self, db: Session, project_id: int) -> None:
        """プロジェクトを論理削除する。

        Args:
            db: DBセッション。
            project_id: 削除対象プロジェクトID。
        """
        project = self.get_project(db, project_id)
        self.repository.soft_delete(db, project)


class ProjectMemberService:
    """プロジェクトメンバーのビジネスロジックを提供する。"""

    def __init__(
        self,
        repository: ProjectMemberRepository | None = None,
        project_repository: ProjectRepository | None = None,
        rbac_repository: RbacRepository | None = None,
        user_repository: UserRepository | None = None,
    ) -> None:
        """ProjectMemberServiceを初期化する。

        Args:
            repository: プロジェクトメンバーRepository。
            project_repository: プロジェクトRepository。
            rbac_repository: RBAC Repository。
            user_repository: ユーザーRepository。
        """
        self.repository = repository or ProjectMemberRepository()
        self.project_repository = project_repository or ProjectRepository()
        self.rbac_repository = rbac_repository or RbacRepository()
        self.user_repository = user_repository or UserRepository()

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

    def add_member(
        self,
        db: Session,
        *,
        project_id: int,
        member_in: ProjectMemberCreate,
    ) -> ProjectMember:
        """プロジェクトメンバーを追加する。

        Args:
            db: DBセッション。
            project_id: プロジェクトID。
            member_in: メンバー追加リクエストの入力値。

        Returns:
            追加されたプロジェクトメンバー。

        Raises:
            ConflictError: 既に所属している場合。
        """
        self._ensure_project_exists(db, project_id)
        self._ensure_user_exists(db, member_in.user_id)
        self._ensure_project_role_exists(db, member_in.role_id)
        existing_member = self.repository.get_by_project_user(
            db,
            project_id=project_id,
            user_id=member_in.user_id,
        )
        if existing_member is not None:
            raise ConflictError("Project member already exists")

        return self.repository.create(
            db,
            project_id=project_id,
            user_id=member_in.user_id,
            role_id=member_in.role_id,
        )

    def update_member(
        self,
        db: Session,
        *,
        project_id: int,
        user_id: int,
        member_in: ProjectMemberUpdate,
    ) -> ProjectMember:
        """プロジェクトメンバーのロールを更新する。

        Args:
            db: DBセッション。
            project_id: プロジェクトID。
            user_id: 更新対象ユーザーID。
            member_in: メンバー更新リクエストの入力値。

        Returns:
            更新されたプロジェクトメンバー。

        Raises:
            VersionConflictError: リクエストのversionが最新ではない場合。
        """
        member = self._get_member(db, project_id=project_id, user_id=user_id)
        if member.version != member_in.version:
            current = ProjectMemberRead.model_validate(member).model_dump()
            raise VersionConflictError(current=current)

        self._ensure_project_role_exists(db, member_in.role_id)
        return self.repository.update_role(db, member=member, role_id=member_in.role_id)

    def remove_member(self, db: Session, *, project_id: int, user_id: int) -> None:
        """プロジェクトメンバーを論理削除する。

        Args:
            db: DBセッション。
            project_id: プロジェクトID。
            user_id: 削除対象ユーザーID。
        """
        member = self._get_member(db, project_id=project_id, user_id=user_id)
        self.repository.soft_delete(db, member)

    def _ensure_project_exists(self, db: Session, project_id: int) -> None:
        """プロジェクトが存在することを確認する。"""
        if self.project_repository.get_by_id(db, project_id) is None:
            raise NotFoundError("Project not found")

    def _ensure_user_exists(self, db: Session, user_id: int) -> None:
        """ユーザーが存在することを確認する。"""
        if self.user_repository.get_by_id(db, user_id) is None:
            raise NotFoundError("User not found")

    def _ensure_project_role_exists(self, db: Session, role_id: int) -> None:
        """プロジェクトロールが存在することを確認する。"""
        role = self.rbac_repository.get_role_by_id(db, role_id)
        if role is None or role.scope != "project":
            raise NotFoundError("Project role not found")

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
            raise NotFoundError("Project member not found")

        return member
