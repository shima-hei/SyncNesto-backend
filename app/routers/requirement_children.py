"""要件の詳細、リンク、コメント、レビューAPIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.routers import requirements_shared as shared
from app.schemas.requirement import (
    RequirementCommentCreate,
    RequirementCommentRead,
    RequirementDetailCreate,
    RequirementDetailRead,
    RequirementDetailUpdate,
    RequirementLinkCreate,
    RequirementLinkRead,
    RequirementRelationCreate,
    RequirementRelationRead,
    RequirementReviewCreate,
    RequirementReviewRead,
    RequirementReviewUpdate,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["requirements"])


@router.post(
    "/requirements/{requirement_id}/details",
    response_model=RequirementDetailRead,
    status_code=status.HTTP_201_CREATED,
)
def create_requirement_detail(
    project_id: int,
    requirement_id: int,
    detail_in: RequirementDetailCreate,
    current_user: User = Depends(require_project_permission("requirement:update")),
    db: Session = Depends(get_db),
) -> RequirementDetailRead:
    """要件詳細を作成する。

    Args:
        project_id: 作成対象のプロジェクトID。
        requirement_id: 作成対象の要件ID。
        detail_in: 要件詳細の作成入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        作成された要件詳細。
    """
    detail = shared.requirement_child_service.create_detail(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        detail_in=detail_in,
        actor_id=current_user.id,
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
    """要件詳細一覧を取得する。

    Args:
        project_id: 取得対象のプロジェクトID。
        requirement_id: 取得対象の要件ID。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        要件詳細一覧。
    """
    details = shared.requirement_child_service.list_details(
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
    current_user: User = Depends(require_project_permission("requirement:update")),
    db: Session = Depends(get_db),
) -> RequirementDetailRead:
    """要件詳細を更新する。

    Args:
        project_id: 更新対象のプロジェクトID。
        requirement_id: 更新対象の要件ID。
        detail_id: 更新対象の要件詳細ID。
        detail_in: 要件詳細の更新入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        更新された要件詳細。
    """
    detail = shared.requirement_child_service.update_detail(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        detail_id=detail_id,
        detail_in=detail_in,
        actor_id=current_user.id,
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
    current_user: User = Depends(require_project_permission("requirement:update")),
    db: Session = Depends(get_db),
) -> None:
    """要件詳細を物理削除する。

    Args:
        project_id: 削除対象のプロジェクトID。
        requirement_id: 削除対象の要件ID。
        detail_id: 削除対象の要件詳細ID。
        current_user: 認証済みユーザー。
        db: DBセッション。
    """
    shared.requirement_child_service.delete_detail(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        detail_id=detail_id,
        actor_id=current_user.id,
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
    current_user: User = Depends(require_project_permission("requirement:link")),
    db: Session = Depends(get_db),
) -> RequirementLinkRead:
    """要件リンクを作成する。

    Args:
        project_id: 作成対象のプロジェクトID。
        requirement_id: 作成対象の要件ID。
        link_in: 要件リンクの作成入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        作成された要件リンク。
    """
    link = shared.requirement_child_service.create_link(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        link_in=link_in,
        actor_id=current_user.id,
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
    """要件リンク一覧を取得する。

    Args:
        project_id: 取得対象のプロジェクトID。
        requirement_id: 取得対象の要件ID。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        要件リンク一覧。
    """
    links = shared.requirement_child_service.list_links(
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
    current_user: User = Depends(require_project_permission("requirement:link")),
    db: Session = Depends(get_db),
) -> None:
    """要件リンクを物理削除する。

    Args:
        project_id: 削除対象のプロジェクトID。
        requirement_id: 削除対象の要件ID。
        link_id: 削除対象の要件リンクID。
        current_user: 認証済みユーザー。
        db: DBセッション。
    """
    shared.requirement_child_service.delete_link(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        link_id=link_id,
        actor_id=current_user.id,
    )


@router.post(
    "/requirements/{requirement_id}/relations",
    response_model=RequirementRelationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_requirement_relation(
    project_id: int,
    requirement_id: int,
    relation_in: RequirementRelationCreate,
    current_user: User = Depends(require_project_permission("requirement:link")),
    db: Session = Depends(get_db),
) -> RequirementRelationRead:
    """要件関連を作成する。

    Args:
        project_id: 作成対象のプロジェクトID。
        requirement_id: 関連元の要件ID。
        relation_in: 要件関連の作成入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        作成された要件関連。
    """
    relation = shared.requirement_child_service.create_relation(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        relation_in=relation_in,
        actor_id=current_user.id,
    )
    return RequirementRelationRead.model_validate(relation)


@router.get(
    "/requirements/{requirement_id}/relations",
    response_model=list[RequirementRelationRead],
)
def list_requirement_relations(
    project_id: int,
    requirement_id: int,
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> list[RequirementRelationRead]:
    """要件関連一覧を取得する。

    Args:
        project_id: 取得対象のプロジェクトID。
        requirement_id: 取得対象の要件ID。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        要件関連一覧。
    """
    relations = shared.requirement_child_service.list_relations(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
    )
    return [
        RequirementRelationRead.model_validate(relation) for relation in relations
    ]


@router.delete(
    "/requirements/{requirement_id}/relations/{relation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_requirement_relation(
    project_id: int,
    requirement_id: int,
    relation_id: int,
    current_user: User = Depends(require_project_permission("requirement:link")),
    db: Session = Depends(get_db),
) -> None:
    """要件関連を物理削除する。

    Args:
        project_id: 削除対象のプロジェクトID。
        requirement_id: 関連元の要件ID。
        relation_id: 削除対象の要件関連ID。
        current_user: 認証済みユーザー。
        db: DBセッション。
    """
    shared.requirement_child_service.delete_relation(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        relation_id=relation_id,
        actor_id=current_user.id,
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
    """要件コメントを作成する。

    Args:
        project_id: 作成対象のプロジェクトID。
        requirement_id: 作成対象の要件ID。
        comment_in: 要件コメントの作成入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        作成された要件コメント。
    """
    comment = shared.requirement_child_service.create_comment(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        user_id=current_user.id,
        comment_in=comment_in,
    )
    return shared.requirement_child_service.build_comment_read(db, comment)


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
    """要件コメント一覧を取得する。

    Args:
        project_id: 取得対象のプロジェクトID。
        requirement_id: 取得対象の要件ID。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        要件コメント一覧。
    """
    return shared.requirement_child_service.list_comment_reads(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
    )


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
    """要件コメントを物理削除する。

    Args:
        project_id: 削除対象のプロジェクトID。
        requirement_id: 削除対象の要件ID。
        comment_id: 削除対象の要件コメントID。
        _: 認可済みユーザー。
        db: DBセッション。
    """
    shared.requirement_child_service.delete_comment(
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
    current_user: User = Depends(require_project_permission("requirement:review")),
    db: Session = Depends(get_db),
) -> RequirementReviewRead:
    """要件レビューを作成する。

    Args:
        project_id: 作成対象のプロジェクトID。
        requirement_id: 作成対象の要件ID。
        review_in: 要件レビューの作成入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        作成された要件レビュー。
    """
    review = shared.requirement_child_service.create_review(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        review_in=review_in,
        actor_id=current_user.id,
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
    """要件レビュー一覧を取得する。

    Args:
        project_id: 取得対象のプロジェクトID。
        requirement_id: 取得対象の要件ID。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        要件レビュー一覧。
    """
    reviews = shared.requirement_child_service.list_reviews(
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
    current_user: User = Depends(require_project_permission("requirement:review")),
    db: Session = Depends(get_db),
) -> RequirementReviewRead:
    """要件レビューを更新する。

    Args:
        project_id: 更新対象のプロジェクトID。
        requirement_id: 更新対象の要件ID。
        review_id: 更新対象の要件レビューID。
        review_in: 要件レビューの更新入力値。
        current_user: 認証済みユーザー。
        db: DBセッション。

    Returns:
        更新された要件レビュー。
    """
    review = shared.requirement_child_service.update_review(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        review_id=review_id,
        review_in=review_in,
        actor_id=current_user.id,
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
    current_user: User = Depends(require_project_permission("requirement:review")),
    db: Session = Depends(get_db),
) -> None:
    """要件レビューを物理削除する。

    Args:
        project_id: 削除対象のプロジェクトID。
        requirement_id: 削除対象の要件ID。
        review_id: 削除対象の要件レビューID。
        current_user: 認証済みユーザー。
        db: DBセッション。
    """
    shared.requirement_child_service.delete_review(
        db,
        project_id=project_id,
        requirement_id=requirement_id,
        review_id=review_id,
        actor_id=current_user.id,
    )
