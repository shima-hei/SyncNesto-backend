"""プロジェクト関連レスポンスのPresenterを定義するモジュール。"""

from app.models.project import ProjectMember
from app.models.rbac import Role
from app.schemas.project import ProjectMemberRead
from app.schemas.user import RoleRead


def build_project_member_response(
    member: ProjectMember,
    role: Role,
) -> ProjectMemberRead:
    """プロジェクトメンバーレスポンスを組み立てる。

    Args:
        member: レスポンスへ変換するプロジェクトメンバー。
        role: メンバーに紐づくプロジェクトロール。

    Returns:
        プロジェクトメンバー読み取りレスポンス。
    """
    return ProjectMemberRead(
        id=member.id,
        project_id=member.project_id,
        user_id=member.user_id,
        role=RoleRead(key=role.key, name=role.name),
        version=member.version,
    )
