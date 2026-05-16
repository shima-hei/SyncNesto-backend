"""RBACに関するデータアクセス処理を定義するモジュール。"""

from sqlalchemy.orm import Session

from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.user import User
from app.models.project import ProjectMember


class RbacRepository:
    """RBACテーブルへのデータアクセス処理を提供するRepository。"""

    def get_role_by_key_scope(
        self,
        db: Session,
        *,
        key: str,
        scope: str,
    ) -> Role | None:
        """keyとscopeに一致するロールを取得する。

        Args:
            db: DBセッション。
            key: ロールkey。
            scope: ロールの適用範囲。

        Returns:
            一致するロール。存在しない場合はNone。
        """
        return db.query(Role).filter(Role.key == key, Role.scope == scope).first()

    def get_permission_by_code(self, db: Session, code: str) -> Permission | None:
        """codeに一致する権限を取得する。

        Args:
            db: DBセッション。
            code: 権限コード。

        Returns:
            一致する権限。存在しない場合はNone。
        """
        return db.query(Permission).filter(Permission.code == code).first()

    def get_role_by_id(self, db: Session, role_id: int) -> Role | None:
        """idに一致するロールを取得する。

        Args:
            db: DBセッション。
            role_id: ロールID。

        Returns:
            一致するロール。存在しない場合はNone。
        """
        return db.query(Role).filter(Role.id == role_id).first()

    def create_role(
        self,
        db: Session,
        *,
        key: str,
        name: str,
        scope: str,
        description: str | None = None,
    ) -> Role:
        """ロールを作成する。

        Args:
            db: DBセッション。
            key: ロールkey。
            name: ロール名。
            scope: ロールの適用範囲。
            description: ロール説明。

        Returns:
            作成されたロール。
        """
        role = Role(key=key, name=name, scope=scope, description=description)
        db.add(role)
        db.flush()
        return role

    def update_role_display(
        self,
        db: Session,
        *,
        role: Role,
        name: str,
        description: str | None = None,
    ) -> Role:
        """ロールの表示名と説明を更新する。

        Args:
            db: DBセッション。
            role: 更新対象ロール。
            name: 表示名。
            description: 説明。

        Returns:
            更新されたロール。
        """
        role.name = name
        role.description = description
        db.flush()
        return role

    def create_permission(
        self,
        db: Session,
        *,
        code: str,
        description: str | None = None,
    ) -> Permission:
        """権限を作成する。

        Args:
            db: DBセッション。
            code: 権限コード。
            description: 権限説明。

        Returns:
            作成された権限。
        """
        permission = Permission(code=code, description=description)
        db.add(permission)
        db.flush()
        return permission

    def add_permission_to_role(
        self,
        db: Session,
        *,
        role: Role,
        permission: Permission,
    ) -> RolePermission:
        """ロールに権限を付与する。

        Args:
            db: DBセッション。
            role: 権限付与対象ロール。
            permission: 付与する権限。

        Returns:
            作成済み、または作成されたロール権限。
        """
        role_permission = (
            db.query(RolePermission)
            .filter(
                RolePermission.role_id == role.id,
                RolePermission.permission_id == permission.id,
            )
            .first()
        )
        if role_permission is not None:
            return role_permission

        role_permission = RolePermission(role_id=role.id, permission_id=permission.id)
        db.add(role_permission)
        db.flush()
        return role_permission

    def assign_role_to_user(
        self,
        db: Session,
        *,
        user: User,
        role: Role,
    ) -> UserRole:
        """ユーザーにシステムロールを付与する。

        Args:
            db: DBセッション。
            user: ロール付与対象ユーザー。
            role: 付与するロール。

        Returns:
            作成済み、または作成されたユーザーロール。
        """
        user_role = (
            db.query(UserRole)
            .filter(UserRole.user_id == user.id, UserRole.role_id == role.id)
            .first()
        )
        if user_role is not None:
            return user_role

        user_role = UserRole(user_id=user.id, role_id=role.id)
        db.add(user_role)
        db.flush()
        return user_role

    def list_system_roles_by_user(self, db: Session, user_id: int) -> list[Role]:
        """ユーザーに付与されたシステムロール一覧を取得する。

        Args:
            db: DBセッション。
            user_id: ユーザーID。

        Returns:
            システムロール一覧。
        """
        return (
            db.query(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .filter(UserRole.user_id == user_id, Role.scope == "system")
            .order_by(Role.id)
            .all()
        )

    def user_has_system_permission(
        self,
        db: Session,
        *,
        user_id: int,
        permission_code: str,
    ) -> bool:
        """ユーザーが指定されたシステム権限を持つか判定する。

        Args:
            db: DBセッション。
            user_id: 判定対象ユーザーID。
            permission_code: 権限コード。

        Returns:
            権限を持つ場合はTrue。
        """
        return (
            db.query(UserRole.id)
            .join(Role, UserRole.role_id == Role.id)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .join(Permission, RolePermission.permission_id == Permission.id)
            .filter(
                UserRole.user_id == user_id,
                Role.scope == "system",
                Permission.code == permission_code,
            )
            .first()
            is not None
        )

    def project_member_has_permission(
        self,
        db: Session,
        *,
        user_id: int,
        project_id: int,
        permission_code: str,
    ) -> bool:
        """プロジェクトメンバーが指定権限を持つか判定する。

        Args:
            db: DBセッション。
            user_id: 判定対象ユーザーID。
            project_id: 判定対象プロジェクトID。
            permission_code: 権限コード。

        Returns:
            権限を持つ場合はTrue。
        """
        return (
            db.query(ProjectMember.id)
            .join(Role, ProjectMember.role_id == Role.id)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .join(Permission, RolePermission.permission_id == Permission.id)
            .filter(
                ProjectMember.user_id == user_id,
                ProjectMember.project_id == project_id,
                ProjectMember.deleted_at.is_(None),
                Role.scope == "project",
                Permission.code == permission_code,
            )
            .first()
            is not None
        )
