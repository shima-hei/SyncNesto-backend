"""要件定義セクションAPIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.requirement import RequirementSection
from app.models.user import User
from app.routers import requirements_shared as shared
from app.schemas.requirement import (
    RequirementSectionCreate,
    RequirementSectionRead,
    RequirementSectionSortUpdate,
    RequirementSectionUpdate,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["requirements"])


@router.post(
    "/requirement-documents/{document_id}/sections",
    response_model=RequirementSectionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_requirement_section(
    project_id: int,
    document_id: int,
    section_in: RequirementSectionCreate,
    current_user: User = Depends(require_project_permission("requirement:update")),
    db: Session = Depends(get_db),
) -> RequirementSection:
    """要件定義セクションを作成する。

    Args:
        project_id: 作成対象のプロジェクトID。
        document_id: 作成対象の要件定義書ID。
        section_in: セクション作成入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        作成された要件定義セクション。
    """
    return shared.section_service.create_section(
        db,
        project_id=project_id,
        document_id=document_id,
        section_in=section_in,
        actor_id=current_user.id,
    )


@router.get(
    "/requirement-documents/{document_id}/sections",
    response_model=list[RequirementSectionRead],
)
def list_requirement_sections(
    project_id: int,
    document_id: int,
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> list[RequirementSection]:
    """要件定義セクション一覧を取得する。

    Args:
        project_id: 取得対象のプロジェクトID。
        document_id: 取得対象の要件定義書ID。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        要件定義セクション一覧。
    """
    return shared.section_service.list_sections(
        db,
        project_id=project_id,
        document_id=document_id,
    )


@router.get(
    "/requirement-sections/{section_id}",
    response_model=RequirementSectionRead,
)
def read_requirement_section(
    project_id: int,
    section_id: int,
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> RequirementSection:
    """要件定義セクションを取得する。

    Args:
        project_id: 取得対象のプロジェクトID。
        section_id: 取得対象の要件定義セクションID。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        取得した要件定義セクション。
    """
    return shared.section_service.get_section(
        db,
        project_id=project_id,
        section_id=section_id,
    )


@router.patch(
    "/requirement-sections/{section_id}",
    response_model=RequirementSectionRead,
)
def update_requirement_section(
    project_id: int,
    section_id: int,
    section_in: RequirementSectionUpdate,
    current_user: User = Depends(require_project_permission("requirement:update")),
    db: Session = Depends(get_db),
) -> RequirementSection:
    """要件定義セクションを更新する。

    Args:
        project_id: 更新対象のプロジェクトID。
        section_id: 更新対象の要件定義セクションID。
        section_in: セクション更新入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        更新された要件定義セクション。
    """
    return shared.section_service.update_section(
        db,
        project_id=project_id,
        section_id=section_id,
        section_in=section_in,
        actor_id=current_user.id,
    )


@router.patch(
    "/requirement-documents/{document_id}/sections/sort-order",
    response_model=list[RequirementSectionRead],
)
def update_requirement_section_sort_order(
    project_id: int,
    document_id: int,
    sort_in: RequirementSectionSortUpdate,
    current_user: User = Depends(require_project_permission("requirement:update")),
    db: Session = Depends(get_db),
) -> list[RequirementSection]:
    """要件定義セクションの表示順を更新する。

    Args:
        project_id: 更新対象のプロジェクトID。
        document_id: 更新対象の要件定義書ID。
        sort_in: 表示順更新入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        更新後の要件定義セクション一覧。
    """
    return shared.section_service.update_sort_order(
        db,
        project_id=project_id,
        document_id=document_id,
        sort_in=sort_in,
        actor_id=current_user.id,
    )


@router.delete(
    "/requirement-sections/{section_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_requirement_section(
    project_id: int,
    section_id: int,
    current_user: User = Depends(require_project_permission("requirement:update")),
    db: Session = Depends(get_db),
) -> None:
    """要件定義セクションを論理削除する。

    Args:
        project_id: 削除対象のプロジェクトID。
        section_id: 削除対象の要件定義セクションID。
        current_user: 認証済みユーザー。
        db: DBセッション。
    """
    shared.section_service.delete_section(
        db,
        project_id=project_id,
        section_id=section_id,
        actor_id=current_user.id,
    )
