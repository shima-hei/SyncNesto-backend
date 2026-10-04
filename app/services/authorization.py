"""認可判定のサービス層を定義するモジュール。"""

from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.tenant import Tenant, TenantMember
from app.models.user import User
from app.repositories.rbac import RbacRepository


class AuthorizationService:
    """ユーザーが操作権限を持つか判定するサービス。"""

    def __init__(self, repository: RbacRepository | None = None) -> None:
        """AuthorizationServiceを初期化する。

        Args:
            repository: RBAC Repository。
        """
        self.repository = repository or RbacRepository()

    def has_system_permission(
        self,
        db: Session,
        *,
        user: User,
        permission_code: str,
    ) -> bool:
        """ユーザーがシステム権限を持つか判定する。

        Args:
            db: DBセッション。
            user: 判定対象ユーザー。
            permission_code: 権限コード。

        Returns:
            権限を持つ場合はTrue。
        """
        return self.repository.user_has_system_permission(
            db,
            user_id=user.id,
            permission_code=permission_code,
        )

    def can_moderate_requirement_comments(
        self,
        db: Session,
        *,
        user: User,
        project_id: int,
    ) -> bool:
        """Project管理権限で要件コメントを管理できるか判定する。

        Args:
            db: DBセッション。
            user: 判定対象ユーザー。

        Returns:
            要件コメントを管理できる場合はTrue。
        """
        return self.has_project_permission(
            db,
            user=user,
            project_id=project_id,
            permission_code="project:update",
        )

    def has_project_permission(
        self,
        db: Session,
        *,
        user: User,
        project_id: int,
        permission_code: str,
    ) -> bool:
        """ユーザーがプロジェクト権限を持つか判定する。

        Args:
            db: DBセッション。
            user: 判定対象ユーザー。
            project_id: 判定対象プロジェクトID。
            permission_code: 権限コード。

        Returns:
            権限を持つ場合はTrue。
        """
        tenant_id = db.info.get("tenant_id")
        if tenant_id is None:
            return False
        if (
            db.query(TenantMember.id)
            .join(Tenant)
            .join(Project, Project.tenant_id == Tenant.id)
            .filter(
                Project.id == project_id,
                Project.deleted_at.is_(None),
                Tenant.id == tenant_id,
                Tenant.status == "active",
                TenantMember.user_id == user.id,
                TenantMember.status == "active",
            )
            .first()
            is None
        ):
            return False

        return self.repository.project_member_has_permission(
            db,
            user_id=user.id,
            project_id=project_id,
            permission_code=permission_code,
        )
