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
