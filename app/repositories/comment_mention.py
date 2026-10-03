"""メンション候補の所属・閲覧権限に関するDB問い合わせ。"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Query, Session

from app.models.project import ProjectMember
from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.user import User


class CommentMentionRepository:
    """候補表示と投稿検証で同じ対象ユーザー集合を使用する。"""

    def eligible_users(
        self, db: Session, project_id: int, permission: str
    ) -> Query[User]:
        """有効な所属ユーザーのうち対象を閲覧可能なユーザーを返す。"""
        roles = (
            select(RolePermission.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .join(Role, Role.id == RolePermission.role_id)
            .where(Permission.code == permission, Role.scope == "project")
        )
        system_users = (
            select(UserRole.user_id)
            .join(RolePermission, RolePermission.role_id == UserRole.role_id)
            .join(Role, Role.id == UserRole.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(Permission.code == permission, Role.scope == "system")
        )
        return (
            db.query(User)
            .join(ProjectMember, ProjectMember.user_id == User.id)
            .filter(
                ProjectMember.project_id == project_id,
                ProjectMember.deleted_at.is_(None),
                User.deleted_at.is_(None),
                User.is_active.is_(True),
                or_(ProjectMember.role_id.in_(roles), User.id.in_(system_users)),
            )
        )
