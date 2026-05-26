"""プロジェクト関連レスポンスのPresenterを定義するモジュール。"""

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import NotFoundError
from app.models.project import ProjectMember
from app.repositories.rbac import RbacRepository
from app.schemas.project import ProjectMemberRead
from app.schemas.user import RoleRead


def build_project_member_response(
    db: Session,
    member: ProjectMember,
) -> ProjectMemberRead:
    """プロジェクトメンバーレスポンスを組み立てる。

    Args:
        db: DBセッション。
        member: レスポンスへ変換するプロジェクトメンバー。

    Returns:
        プロジェクトメンバー読み取りレスポンス。

    Raises:
        NotFoundError: 紐づくロールが存在しない場合。
    """
    role = RbacRepository().get_role_by_id(db, member.role_id)
    if role is None:
        raise NotFoundError(error_messages.PROJECT_ROLE_NOT_FOUND)

    return ProjectMemberRead(
        id=member.id,
        project_id=member.project_id,
        user_id=member.user_id,
        role=RoleRead(key=role.key, name=role.name),
        version=member.version,
    )
