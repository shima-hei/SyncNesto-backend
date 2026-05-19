"""プロジェクトAPIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.auth import (
    get_current_user,
    require_project_permission,
    require_system_permission,
)
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.project import Project, ProjectMember
from app.models.user import User
from app.repositories.rbac import RbacRepository
from app.schemas.project import (
    ProjectCreate,
    ProjectListItem,
    ProjectListResponse,
    ProjectMemberCreate,
    ProjectMemberRead,
    ProjectMemberUpdate,
    ProjectRead,
    ProjectUpdate,
)
from app.schemas.user import RoleRead
from app.services.project import ProjectMemberService, ProjectService


router = APIRouter(prefix="/projects", tags=["projects"])
project_service = ProjectService()
project_member_service = ProjectMemberService()


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
        raise NotFoundError("Project role not found")

    return ProjectMemberRead(
        id=member.id,
        project_id=member.project_id,
        user_id=member.user_id,
        role=RoleRead(key=role.key, name=role.name),
        version=member.version,
    )


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
)
def create_project(
    project_in: ProjectCreate,
    current_user: User = Depends(require_system_permission("project:create")),
    db: Session = Depends(get_db),
) -> Project:
    """プロジェクトを作成する。

    Args:
        project_in: プロジェクト作成リクエストの入力値。
        current_user: 認可済みユーザー。
        db: DBセッション。

    Returns:
        作成されたプロジェクト。
    """
    return project_service.create_project(db, project_in, actor_id=current_user.id)


@router.get(
    "",
    response_model=ProjectListResponse,
)
def list_projects(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectListResponse:
    """プロジェクト一覧を取得する。

    Args:
        page: ページ番号。
        page_size: 1ページあたりの件数。
        q: 検索キーワード。
        status: ステータス絞り込み。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        閲覧可能なプロジェクト一覧。
    """
    projects, total = project_service.list_projects_paginated(
        db,
        current_user,
        page=page,
        page_size=page_size,
        q=q,
        status=status,
    )
    return ProjectListResponse(
        items=[ProjectListItem.model_validate(project) for project in projects],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{project_id}",
    response_model=ProjectRead,
)
def read_project(
    project_id: int,
    _: User = Depends(require_project_permission("project:read")),
    db: Session = Depends(get_db),
) -> Project:
    """プロジェクトを取得する。

    Args:
        project_id: 取得対象プロジェクトID。
        db: DBセッション。

    Returns:
        取得されたプロジェクト。
    """
    return project_service.get_project(db, project_id)


@router.patch(
    "/{project_id}",
    response_model=ProjectRead,
)
def update_project(
    project_id: int,
    project_in: ProjectUpdate,
    current_user: User = Depends(require_project_permission("project:update")),
    db: Session = Depends(get_db),
) -> Project:
    """プロジェクトを更新する。

    Args:
        project_id: 更新対象プロジェクトID。
        project_in: プロジェクト更新リクエストの入力値。
        current_user: 認可済みユーザー。
        db: DBセッション。

    Returns:
        更新されたプロジェクト。
    """
    return project_service.update_project(
        db,
        project_id,
        project_in,
        actor_id=current_user.id,
    )


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_project(
    project_id: int,
    current_user: User = Depends(require_project_permission("project:delete")),
    db: Session = Depends(get_db),
) -> None:
    """プロジェクトを論理削除する。

    Args:
        project_id: 削除対象プロジェクトID。
        current_user: 認可済みユーザー。
        db: DBセッション。
    """
    project_service.delete_project(db, project_id, actor_id=current_user.id)


@router.post(
    "/{project_id}/members",
    response_model=ProjectMemberRead,
    status_code=status.HTTP_201_CREATED,
)
def add_project_member(
    project_id: int,
    member_in: ProjectMemberCreate,
    _: User = Depends(require_project_permission("project:invite_member")),
    db: Session = Depends(get_db),
) -> ProjectMemberRead:
    """プロジェクトメンバーを追加する。

    Args:
        project_id: プロジェクトID。
        member_in: メンバー追加リクエストの入力値。
        db: DBセッション。

    Returns:
        追加されたプロジェクトメンバー。
    """
    member = project_member_service.add_member(
        db,
        project_id=project_id,
        member_in=member_in,
    )
    return build_project_member_response(db, member)


@router.get(
    "/{project_id}/members",
    response_model=list[ProjectMemberRead],
)
def list_project_members(
    project_id: int,
    _: User = Depends(require_project_permission("project:read")),
    db: Session = Depends(get_db),
) -> list[ProjectMemberRead]:
    """プロジェクトメンバー一覧を取得する。

    Args:
        project_id: プロジェクトID。
        db: DBセッション。

    Returns:
        プロジェクトメンバー一覧。
    """
    members = project_member_service.list_members(db, project_id)
    return [build_project_member_response(db, member) for member in members]


@router.patch(
    "/{project_id}/members/{user_id}",
    response_model=ProjectMemberRead,
)
def update_project_member(
    project_id: int,
    user_id: int,
    member_in: ProjectMemberUpdate,
    _: User = Depends(require_project_permission("project:invite_member")),
    db: Session = Depends(get_db),
) -> ProjectMemberRead:
    """プロジェクトメンバーのロールを更新する。

    Args:
        project_id: プロジェクトID。
        user_id: 更新対象ユーザーID。
        member_in: メンバー更新リクエストの入力値。
        db: DBセッション。

    Returns:
        更新されたプロジェクトメンバー。
    """
    member = project_member_service.update_member(
        db,
        project_id=project_id,
        user_id=user_id,
        member_in=member_in,
    )
    return build_project_member_response(db, member)


@router.delete(
    "/{project_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_project_member(
    project_id: int,
    user_id: int,
    _: User = Depends(require_project_permission("project:remove_member")),
    db: Session = Depends(get_db),
) -> None:
    """プロジェクトメンバーを物理削除する。

    Args:
        project_id: プロジェクトID。
        user_id: 削除対象ユーザーID。
        db: DBセッション。
    """
    project_member_service.remove_member(db, project_id=project_id, user_id=user_id)
