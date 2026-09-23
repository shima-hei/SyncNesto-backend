"""プロジェクト関連レスポンスのPresenterを定義するモジュール。"""

from app.models.project import Project, ProjectMember
from app.models.rbac import Role
from app.models.user import User
from app.schemas.project import (
    CurrentProjectRoleRead,
    ProjectListItem,
    ProjectListResponse,
    ProjectMemberRead,
)
from app.schemas.user import RoleRead, UserSummaryListResponse
from app.services.storage import StorageService

from .user import build_user_summary


def build_project_list_response(
    projects: list[Project],
    *,
    total: int,
    page: int,
    page_size: int,
) -> ProjectListResponse:
    """プロジェクト一覧レスポンスを組み立てる。

    Args:
        projects: レスポンスへ変換するプロジェクト一覧。
        total: 全件数。
        page: ページ番号。
        page_size: 1ページあたりの件数。

    Returns:
        プロジェクト一覧レスポンス。
    """
    return ProjectListResponse(
        items=[ProjectListItem.model_validate(project) for project in projects],
        total=total,
        page=page,
        page_size=page_size,
    )


def build_current_project_role_response(
    *,
    project_id: int,
    role: Role | None,
    is_system_admin: bool,
) -> CurrentProjectRoleRead:
    """現在ユーザーのプロジェクトロールレスポンスを組み立てる。

    Args:
        project_id: 対象プロジェクトID。
        role: 対象プロジェクト内ロール。
        is_system_admin: system_adminとしてアクセスしているか。

    Returns:
        現在ユーザーのプロジェクトロールレスポンス。
    """
    return CurrentProjectRoleRead(
        project_id=project_id,
        role=RoleRead.model_validate(role) if role is not None else None,
        is_system_admin=is_system_admin,
    )


def build_project_member_user_list_response(
    users: list[User],
    storage_service: StorageService,
) -> UserSummaryListResponse:
    """プロジェクト所属ユーザー一覧レスポンスを組み立てる。

    Args:
        users: プロジェクト所属ユーザー一覧。
        storage_service: avatar_url生成に使用するストレージサービス。

    Returns:
        軽量ユーザー一覧レスポンス。
    """
    return UserSummaryListResponse(
        items=[build_user_summary(user, storage_service) for user in users],
    )


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


def build_project_member_responses(
    members: list[ProjectMember],
    roles_by_id: dict[int, Role],
) -> list[ProjectMemberRead]:
    """プロジェクトメンバー一覧レスポンスを組み立てる。

    Args:
        members: レスポンスへ変換するプロジェクトメンバー一覧。
        roles_by_id: ロールIDをkeyにしたロール辞書。

    Returns:
        プロジェクトメンバーレスポンス一覧。
    """
    return [
        build_project_member_response(member, roles_by_id[member.role_id])
        for member in members
    ]
