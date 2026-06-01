"""要件定義APIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.requirement import Requirement
from app.models.user import User
from app.presenters.requirement import (
    build_requirement_document_response_with_users,
    build_requirement_document_responses_with_users,
)
from app.schemas.requirement import (
    RequirementCommentCreate,
    RequirementCommentRead,
    RequirementCreate,
    RequirementDetailCreate,
    RequirementDetailRead,
    RequirementDetailUpdate,
    RequirementDocumentCreate,
    RequirementDocumentListResponse,
    RequirementDocumentRead,
    RequirementDocumentUpdate,
    RequirementLinkCreate,
    RequirementLinkRead,
    RequirementListResponse,
    RequirementRead,
    RequirementReviewCreate,
    RequirementReviewRead,
    RequirementReviewUpdate,
    RequirementRevisionRead,
    RequirementSummaryRead,
    RequirementUpdate,
)
from app.services.requirement import (
    RequirementChildService,
    RequirementDocumentService,
    RequirementService,
)
from app.services.storage import StorageService

router = APIRouter(prefix="/projects/{project_id}", tags=["requirements"])
document_service = RequirementDocumentService()
requirement_service = RequirementService()
requirement_child_service = RequirementChildService()
storage_service = StorageService()


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
    """要件定義書を作成する。"""
    document = document_service.create_document(
        db,
        project_id=project_id,
        document_in=document_in,
        actor_id=current_user.id,
    )
    return build_requirement_document_response_with_users(
        db,
        document,
        storage_service,
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
    """要件定義書一覧を取得する。"""
    documents, total = document_service.list_documents_paginated(
        db,
        project_id=project_id,
        page=page,
        page_size=page_size,
        q=q,
        status=status,
    )
    return RequirementDocumentListResponse(
        items=build_requirement_document_responses_with_users(
            db,
            documents,
            storage_service,
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
    """要件定義書を取得する。"""
    document = document_service.get_document(
        db,
        project_id=project_id,
        document_id=document_id,
    )
    return build_requirement_document_response_with_users(
        db,
        document,
        storage_service,
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
    """要件定義書を更新する。"""
    document = document_service.update_document(
        db,
        project_id=project_id,
        document_id=document_id,
        document_in=document_in,
        actor_id=current_user.id,
    )
    return build_requirement_document_response_with_users(
        db,
        document,
        storage_service,
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
    """要件定義書を論理削除する。"""
    document_service.delete_document(
        db,
        project_id=project_id,
        document_id=document_id,
        actor_id=current_user.id,
    )


@router.post(
    "/requirements",
    response_model=RequirementRead,
    status_code=status.HTTP_201_CREATED,
)
def create_requirement(
    project_id: int,
    requirement_in: RequirementCreate,
    current_user: User = Depends(require_project_permission("requirement:create")),
    db: Session = Depends(get_db),
) -> Requirement:
    """要件を作成する。"""
    return requirement_service.create_requirement(
        db,
        project_id=project_id,
        requirement_in=requirement_in,
        actor_id=current_user.id,
    )


@router.get(
    "/requirements",
    response_model=RequirementListResponse,
)
def list_requirements(
    project_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    document_id: int | None = Query(default=None),
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    requirement_type: str | None = Query(default=None),
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> RequirementListResponse:
    """要件一覧を取得する。"""
    requirements, total = requirement_service.list_requirements_paginated(
        db,
        project_id=project_id,
        page=page,
        page_size=page_size,
        document_id=document_id,
        q=q,
        status=status,
        requirement_type=requirement_type,
    )
    return RequirementListResponse(
        items=[
            RequirementRead.model_validate(requirement) for requirement in requirements
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/requirements/{requirement_id}",
    response_model=RequirementRead,
)
def read_requirement(
    project_id: int,
    requirement_id: int,
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> Requirement:
    """要件を取得する。"""
    return requirement_service.get_requirement(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
    )


@router.get(
    "/requirements/{requirement_id}/summary",
    response_model=RequirementSummaryRead,
)
def read_requirement_summary(
    project_id: int,
    requirement_id: int,
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> RequirementSummaryRead:
    """要件詳細画面用の集約情報を取得する。"""
    summary = requirement_child_service.get_summary(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
    )
    return RequirementSummaryRead(
        requirement=RequirementRead.model_validate(summary["requirement"]),
        details=[
            RequirementDetailRead.model_validate(detail)
            for detail in summary["details"]
        ],
        links=[RequirementLinkRead.model_validate(link) for link in summary["links"]],
        comments=[
            RequirementCommentRead.model_validate(comment)
            for comment in summary["comments"]
        ],
        reviews=[
            RequirementReviewRead.model_validate(review)
            for review in summary["reviews"]
        ],
        revisions=[
            RequirementRevisionRead.model_validate(revision)
            for revision in summary["revisions"]
        ],
    )


@router.patch(
    "/requirements/{requirement_id}",
    response_model=RequirementRead,
)
def update_requirement(
    project_id: int,
    requirement_id: int,
    requirement_in: RequirementUpdate,
    current_user: User = Depends(require_project_permission("requirement:update")),
    db: Session = Depends(get_db),
) -> Requirement:
    """要件を更新する。"""
    return requirement_service.update_requirement(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        requirement_in=requirement_in,
        actor_id=current_user.id,
    )


@router.delete(
    "/requirements/{requirement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_requirement(
    project_id: int,
    requirement_id: int,
    current_user: User = Depends(require_project_permission("requirement:delete")),
    db: Session = Depends(get_db),
) -> None:
    """要件を論理削除する。"""
    requirement_service.delete_requirement(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        actor_id=current_user.id,
    )


@router.get(
    "/requirements/{requirement_id}/revisions",
    response_model=list[RequirementRevisionRead],
)
def list_requirement_revisions(
    project_id: int,
    requirement_id: int,
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> list[RequirementRevisionRead]:
    """要件の改訂履歴一覧を取得する。"""
    revisions = requirement_service.list_revisions(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
    )
    return [RequirementRevisionRead.model_validate(revision) for revision in revisions]


@router.post(
    "/requirements/{requirement_id}/details",
    response_model=RequirementDetailRead,
    status_code=status.HTTP_201_CREATED,
)
def create_requirement_detail(
    project_id: int,
    requirement_id: int,
    detail_in: RequirementDetailCreate,
    _: User = Depends(require_project_permission("requirement:update")),
    db: Session = Depends(get_db),
) -> RequirementDetailRead:
    """要件詳細を作成する。"""
    detail = requirement_child_service.create_detail(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        detail_in=detail_in,
    )
    return RequirementDetailRead.model_validate(detail)


@router.get(
    "/requirements/{requirement_id}/details",
    response_model=list[RequirementDetailRead],
)
def list_requirement_details(
    project_id: int,
    requirement_id: int,
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> list[RequirementDetailRead]:
    """要件詳細一覧を取得する。"""
    details = requirement_child_service.list_details(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
    )
    return [RequirementDetailRead.model_validate(detail) for detail in details]


@router.patch(
    "/requirements/{requirement_id}/details/{detail_id}",
    response_model=RequirementDetailRead,
)
def update_requirement_detail(
    project_id: int,
    requirement_id: int,
    detail_id: int,
    detail_in: RequirementDetailUpdate,
    _: User = Depends(require_project_permission("requirement:update")),
    db: Session = Depends(get_db),
) -> RequirementDetailRead:
    """要件詳細を更新する。"""
    detail = requirement_child_service.update_detail(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        detail_id=detail_id,
        detail_in=detail_in,
    )
    return RequirementDetailRead.model_validate(detail)


@router.delete(
    "/requirements/{requirement_id}/details/{detail_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_requirement_detail(
    project_id: int,
    requirement_id: int,
    detail_id: int,
    _: User = Depends(require_project_permission("requirement:update")),
    db: Session = Depends(get_db),
) -> None:
    """要件詳細を物理削除する。"""
    requirement_child_service.delete_detail(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        detail_id=detail_id,
    )


@router.post(
    "/requirements/{requirement_id}/links",
    response_model=RequirementLinkRead,
    status_code=status.HTTP_201_CREATED,
)
def create_requirement_link(
    project_id: int,
    requirement_id: int,
    link_in: RequirementLinkCreate,
    _: User = Depends(require_project_permission("requirement:link")),
    db: Session = Depends(get_db),
) -> RequirementLinkRead:
    """要件リンクを作成する。"""
    link = requirement_child_service.create_link(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        link_in=link_in,
    )
    return RequirementLinkRead.model_validate(link)


@router.get(
    "/requirements/{requirement_id}/links",
    response_model=list[RequirementLinkRead],
)
def list_requirement_links(
    project_id: int,
    requirement_id: int,
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> list[RequirementLinkRead]:
    """要件リンク一覧を取得する。"""
    links = requirement_child_service.list_links(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
    )
    return [RequirementLinkRead.model_validate(link) for link in links]


@router.delete(
    "/requirements/{requirement_id}/links/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_requirement_link(
    project_id: int,
    requirement_id: int,
    link_id: int,
    _: User = Depends(require_project_permission("requirement:link")),
    db: Session = Depends(get_db),
) -> None:
    """要件リンクを物理削除する。"""
    requirement_child_service.delete_link(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        link_id=link_id,
    )


@router.post(
    "/requirements/{requirement_id}/comments",
    response_model=RequirementCommentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_requirement_comment(
    project_id: int,
    requirement_id: int,
    comment_in: RequirementCommentCreate,
    current_user: User = Depends(require_project_permission("requirement:comment")),
    db: Session = Depends(get_db),
) -> RequirementCommentRead:
    """要件コメントを作成する。"""
    comment = requirement_child_service.create_comment(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        user_id=current_user.id,
        comment_in=comment_in,
    )
    return RequirementCommentRead.model_validate(comment)


@router.get(
    "/requirements/{requirement_id}/comments",
    response_model=list[RequirementCommentRead],
)
def list_requirement_comments(
    project_id: int,
    requirement_id: int,
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> list[RequirementCommentRead]:
    """要件コメント一覧を取得する。"""
    comments = requirement_child_service.list_comments(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
    )
    return [RequirementCommentRead.model_validate(comment) for comment in comments]


@router.delete(
    "/requirements/{requirement_id}/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_requirement_comment(
    project_id: int,
    requirement_id: int,
    comment_id: int,
    _: User = Depends(require_project_permission("requirement:comment")),
    db: Session = Depends(get_db),
) -> None:
    """要件コメントを物理削除する。"""
    requirement_child_service.delete_comment(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        comment_id=comment_id,
    )


@router.post(
    "/requirements/{requirement_id}/reviews",
    response_model=RequirementReviewRead,
    status_code=status.HTTP_201_CREATED,
)
def create_requirement_review(
    project_id: int,
    requirement_id: int,
    review_in: RequirementReviewCreate,
    _: User = Depends(require_project_permission("requirement:review")),
    db: Session = Depends(get_db),
) -> RequirementReviewRead:
    """要件レビューを作成する。"""
    review = requirement_child_service.create_review(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        review_in=review_in,
    )
    return RequirementReviewRead.model_validate(review)


@router.get(
    "/requirements/{requirement_id}/reviews",
    response_model=list[RequirementReviewRead],
)
def list_requirement_reviews(
    project_id: int,
    requirement_id: int,
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> list[RequirementReviewRead]:
    """要件レビュー一覧を取得する。"""
    reviews = requirement_child_service.list_reviews(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
    )
    return [RequirementReviewRead.model_validate(review) for review in reviews]


@router.patch(
    "/requirements/{requirement_id}/reviews/{review_id}",
    response_model=RequirementReviewRead,
)
def update_requirement_review(
    project_id: int,
    requirement_id: int,
    review_id: int,
    review_in: RequirementReviewUpdate,
    _: User = Depends(require_project_permission("requirement:review")),
    db: Session = Depends(get_db),
) -> RequirementReviewRead:
    """要件レビューを更新する。"""
    review = requirement_child_service.update_review(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        review_id=review_id,
        review_in=review_in,
    )
    return RequirementReviewRead.model_validate(review)


@router.delete(
    "/requirements/{requirement_id}/reviews/{review_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_requirement_review(
    project_id: int,
    requirement_id: int,
    review_id: int,
    _: User = Depends(require_project_permission("requirement:review")),
    db: Session = Depends(get_db),
) -> None:
    """要件レビューを物理削除する。"""
    requirement_child_service.delete_review(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        review_id=review_id,
    )
