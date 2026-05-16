"""認可判定のサービス層を定義するモジュール。"""

from sqlalchemy.orm import Session

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
        if self.has_system_permission(
            db,
            user=user,
            permission_code=permission_code,
        ):
            return True

        return self.repository.project_member_has_permission(
            db,
            user_id=user.id,
            project_id=project_id,
            permission_code=permission_code,
        )
