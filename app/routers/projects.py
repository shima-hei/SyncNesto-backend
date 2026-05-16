"""プロジェクトAPIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.auth import (
    get_current_user,
    require_project_permission,
    require_system_permission,
)
from app.db.session import get_db
from app.models.project import Project, ProjectMember
from app.models.user import User
from app.schemas.project import (
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberRead,
    ProjectMemberUpdate,
    ProjectRead,
    ProjectUpdate,
)
from app.services.project import ProjectMemberService, ProjectService


router = APIRouter(prefix="/projects", tags=["projects"])
project_service = ProjectService()
project_member_service = ProjectMemberService()


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
)
def create_project(
    project_in: ProjectCreate,
    _: User = Depends(require_system_permission("project:create")),
    db: Session = Depends(get_db),
) -> Project:
    """プロジェクトを作成する。

    Args:
        project_in: プロジェクト作成リクエストの入力値。
        db: DBセッション。

    Returns:
        作成されたプロジェクト。
    """
    return project_service.create_project(db, project_in)


@router.get(
    "",
    response_model=list[ProjectRead],
)
def list_projects(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Project]:
    """プロジェクト一覧を取得する。

    Args:
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        閲覧可能なプロジェクト一覧。
    """
    return project_service.list_projects(db, current_user)


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
    _: User = Depends(require_project_permission("project:update")),
    db: Session = Depends(get_db),
) -> Project:
    """プロジェクトを更新する。

    Args:
        project_id: 更新対象プロジェクトID。
        project_in: プロジェクト更新リクエストの入力値。
        db: DBセッション。

    Returns:
        更新されたプロジェクト。
    """
    return project_service.update_project(db, project_id, project_in)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_project(
    project_id: int,
    _: User = Depends(require_project_permission("project:delete")),
    db: Session = Depends(get_db),
) -> None:
    """プロジェクトを論理削除する。

    Args:
        project_id: 削除対象プロジェクトID。
        db: DBセッション。
    """
    project_service.delete_project(db, project_id)


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
) -> ProjectMember:
    """プロジェクトメンバーを追加する。

    Args:
        project_id: プロジェクトID。
        member_in: メンバー追加リクエストの入力値。
        db: DBセッション。

    Returns:
        追加されたプロジェクトメンバー。
    """
    return project_member_service.add_member(
        db,
        project_id=project_id,
        member_in=member_in,
    )


@router.get(
    "/{project_id}/members",
    response_model=list[ProjectMemberRead],
)
def list_project_members(
    project_id: int,
    _: User = Depends(require_project_permission("project:read")),
    db: Session = Depends(get_db),
) -> list[ProjectMember]:
    """プロジェクトメンバー一覧を取得する。

    Args:
        project_id: プロジェクトID。
        db: DBセッション。

    Returns:
        プロジェクトメンバー一覧。
    """
    return project_member_service.list_members(db, project_id)


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
) -> ProjectMember:
    """プロジェクトメンバーのロールを更新する。

    Args:
        project_id: プロジェクトID。
        user_id: 更新対象ユーザーID。
        member_in: メンバー更新リクエストの入力値。
        db: DBセッション。

    Returns:
        更新されたプロジェクトメンバー。
    """
    return project_member_service.update_member(
        db,
        project_id=project_id,
        user_id=user_id,
        member_in=member_in,
    )


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
    """プロジェクトメンバーを論理削除する。

    Args:
        project_id: プロジェクトID。
        user_id: 削除対象ユーザーID。
        db: DBセッション。
    """
    project_member_service.remove_member(db, project_id=project_id, user_id=user_id)
