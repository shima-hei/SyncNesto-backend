"""要件定義書APIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.presenters.requirement import (
    build_requirement_document_response,
    build_requirement_document_responses,
)
from app.routers import requirements_shared as shared
from app.schemas.requirement import (
    RequirementDocumentCreate,
    RequirementDocumentExportCreate,
    RequirementDocumentExportRead,
    RequirementDocumentListResponse,
    RequirementDocumentRead,
    RequirementDocumentUpdate,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["requirements"])


@router.post(
    "/requirement-documents",
    response_model=RequirementDocumentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_requirement_document(
    project_id: int,
    document_in: RequirementDocumentCreate,
    current_user: User = Depends(require_project_permission("requirement:create")),
    db: Session = Depends(get_db),
) -> RequirementDocumentRead:
    """要件定義書を作成する。

    Args:
        project_id: 作成対象のプロジェクトID。
        document_in: 要件定義書の作成入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        作成された要件定義書。
    """
    document = shared.document_service.create_document(
        db,
        project_id=project_id,
        document_in=document_in,
        actor_id=current_user.id,
    )
    return build_requirement_document_response(
        document,
        shared.get_requirement_document_users_by_id(db, [document]),
        shared.storage_service,
    )


@router.get(
    "/requirement-documents",
    response_model=RequirementDocumentListResponse,
)
def list_requirement_documents(
    project_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> RequirementDocumentListResponse:
    """要件定義書一覧を取得する。

    Args:
        project_id: 一覧取得対象のプロジェクトID。
        page: 取得ページ番号。
        page_size: 1ページあたりの取得件数。
        q: 検索キーワード。
        status: 絞り込み対象のステータス。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        要件定義書のページング済み一覧。
    """
    documents, total = shared.document_service.list_documents_paginated(
        db,
        project_id=project_id,
        page=page,
        page_size=page_size,
        q=q,
        status=status,
    )
    return RequirementDocumentListResponse(
        items=build_requirement_document_responses(
            documents,
            shared.get_requirement_document_users_by_id(db, documents),
            shared.storage_service,
        ),
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/requirement-documents/{document_id}",
    response_model=RequirementDocumentRead,
)
def read_requirement_document(
    project_id: int,
    document_id: int,
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> RequirementDocumentRead:
    """要件定義書を取得する。

    Args:
        project_id: 取得対象のプロジェクトID。
        document_id: 取得対象の要件定義書ID。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        取得した要件定義書。
    """
    document = shared.document_service.get_document(
        db,
        project_id=project_id,
        document_id=document_id,
    )
    return build_requirement_document_response(
        document,
        shared.get_requirement_document_users_by_id(db, [document]),
        shared.storage_service,
    )


@router.patch(
    "/requirement-documents/{document_id}",
    response_model=RequirementDocumentRead,
)
def update_requirement_document(
    project_id: int,
    document_id: int,
    document_in: RequirementDocumentUpdate,
    current_user: User = Depends(require_project_permission("requirement:update")),
    db: Session = Depends(get_db),
) -> RequirementDocumentRead:
    """要件定義書を更新する。

    Args:
        project_id: 更新対象のプロジェクトID。
        document_id: 更新対象の要件定義書ID。
        document_in: 要件定義書の更新入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        更新された要件定義書。
    """
    document = shared.document_service.update_document(
        db,
        project_id=project_id,
        document_id=document_id,
        document_in=document_in,
        actor_id=current_user.id,
    )
    return build_requirement_document_response(
        document,
        shared.get_requirement_document_users_by_id(db, [document]),
        shared.storage_service,
    )


@router.delete(
    "/requirement-documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_requirement_document(
    project_id: int,
    document_id: int,
    current_user: User = Depends(require_project_permission("requirement:delete")),
    db: Session = Depends(get_db),
) -> None:
    """要件定義書を論理削除する。

    Args:
        project_id: 削除対象のプロジェクトID。
        document_id: 削除対象の要件定義書ID。
        current_user: 認証済みユーザー。
        db: DBセッション。
    """
    shared.document_service.delete_document(
        db,
        project_id=project_id,
        document_id=document_id,
        actor_id=current_user.id,
    )


@router.post(
    "/requirement-documents/{document_id}/exports",
    response_model=RequirementDocumentExportRead,
)
def export_requirement_document(
    project_id: int,
    document_id: int,
    export_in: RequirementDocumentExportCreate,
    current_user: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> RequirementDocumentExportRead:
    """要件定義書を出力する。

    Args:
        project_id: 出力対象のプロジェクトID。
        document_id: 出力対象の要件定義書ID。
        export_in: 出力リクエスト。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        要件定義書の出力結果。
    """
    return shared.export_service.export_document(
        db,
        project_id=project_id,
        document_id=document_id,
        export_in=export_in,
        actor_id=current_user.id,
    )
