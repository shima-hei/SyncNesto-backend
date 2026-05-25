"""要件定義関連のサービス層を定義するモジュール。"""

from typing import TypedDict

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import (
    DuplicateResourceError,
    NotFoundError,
    VersionConflictError,
)
from app.models.requirement import (
    Requirement,
    RequirementComment,
    RequirementDetail,
    RequirementDocument,
    RequirementLink,
    RequirementReview,
    RequirementRevision,
)
from app.repositories.project import ProjectRepository
from app.repositories.requirement import (
    RequirementCommentRepository,
    RequirementDetailRepository,
    RequirementDocumentRepository,
    RequirementLinkRepository,
    RequirementRepository,
    RequirementReviewRepository,
    RequirementRevisionRepository,
)
from app.schemas.requirement import (
    RequirementCommentCreate,
    RequirementCreate,
    RequirementDetailCreate,
    RequirementDetailUpdate,
    RequirementDocumentCreate,
    RequirementDocumentRead,
    RequirementDocumentUpdate,
    RequirementLinkCreate,
    RequirementRead,
    RequirementReviewCreate,
    RequirementReviewUpdate,
    RequirementUpdate,
)


class RequirementSummary(TypedDict):
    """要件詳細画面用の集約情報型。"""

    requirement: Requirement
    details: list[RequirementDetail]
    links: list[RequirementLink]
    comments: list[RequirementComment]
    reviews: list[RequirementReview]
    revisions: list[RequirementRevision]


class RequirementDocumentService:
    """要件定義書に関するビジネスロジックを提供する。"""

    def __init__(
        self,
        repository: RequirementDocumentRepository | None = None,
        project_repository: ProjectRepository | None = None,
    ) -> None:
        """RequirementDocumentServiceを初期化する。"""
        self.repository = repository or RequirementDocumentRepository()
        self.project_repository = project_repository or ProjectRepository()

    def create_document(
        self,
        db: Session,
        *,
        project_id: int,
        document_in: RequirementDocumentCreate,
        actor_id: int | None = None,
    ) -> RequirementDocument:
        """要件定義書を作成する。"""
        self._ensure_project_exists(db, project_id)
        if (
            self.repository.get_by_project_document_code(
                db,
                project_id=project_id,
                document_code=document_in.document_code,
            )
            is not None
        ):
            raise DuplicateResourceError(
                error_messages.REQUIREMENT_DOCUMENT_CODE_ALREADY_EXISTS
            )

        try:
            return self.repository.create(
                db,
                project_id=project_id,
                document_in=document_in,
                actor_id=actor_id,
            )
        except IntegrityError as exc:
            db.rollback()
            raise DuplicateResourceError(
                error_messages.REQUIREMENT_DOCUMENT_CODE_ALREADY_EXISTS
            ) from exc

    def list_documents_paginated(
        self,
        db: Session,
        *,
        project_id: int,
        page: int,
        page_size: int,
        q: str | None = None,
        status: str | None = None,
    ) -> tuple[list[RequirementDocument], int]:
        """プロジェクト内の要件定義書一覧をページング付きで取得する。"""
        self._ensure_project_exists(db, project_id)
        return self.repository.list_paginated(
            db,
            project_id=project_id,
            page=page,
            page_size=page_size,
            q=q,
            status=status,
        )

    def get_document(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
    ) -> RequirementDocument:
        """要件定義書を取得する。"""
        document = self.repository.get_by_id(db, document_id)
        if document is None or document.project_id != project_id:
            raise NotFoundError(error_messages.REQUIREMENT_DOCUMENT_NOT_FOUND)
        return document

    def update_document(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
        document_in: RequirementDocumentUpdate,
        actor_id: int | None = None,
    ) -> RequirementDocument:
        """要件定義書を更新する。"""
        document = self.get_document(db, project_id=project_id, document_id=document_id)
        if document.version != document_in.version:
            current = RequirementDocumentRead.model_validate(document).model_dump()
            raise VersionConflictError(current=current)

        if (
            document_in.document_code is not None
            and document_in.document_code != document.document_code
            and self.repository.get_by_project_document_code(
                db,
                project_id=project_id,
                document_code=document_in.document_code,
            )
            is not None
        ):
            raise DuplicateResourceError(
                error_messages.REQUIREMENT_DOCUMENT_CODE_ALREADY_EXISTS
            )

        try:
            return self.repository.update(
                db,
                document=document,
                document_in=document_in,
                actor_id=actor_id,
            )
        except IntegrityError as exc:
            db.rollback()
            raise DuplicateResourceError(
                error_messages.REQUIREMENT_DOCUMENT_CODE_ALREADY_EXISTS
            ) from exc

    def delete_document(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
        actor_id: int | None = None,
    ) -> None:
        """要件定義書を論理削除する。"""
        document = self.get_document(db, project_id=project_id, document_id=document_id)
        self.repository.soft_delete(db, document=document, actor_id=actor_id)

    def _ensure_project_exists(self, db: Session, project_id: int) -> None:
        """プロジェクトが存在することを確認する。"""
        if self.project_repository.get_by_id(db, project_id) is None:
            raise NotFoundError(error_messages.PROJECT_NOT_FOUND)


class RequirementService:
    """要件に関するビジネスロジックを提供する。"""

    def __init__(
        self,
        repository: RequirementRepository | None = None,
        document_repository: RequirementDocumentRepository | None = None,
        revision_repository: RequirementRevisionRepository | None = None,
    ) -> None:
        """RequirementServiceを初期化する。"""
        self.repository = repository or RequirementRepository()
        self.document_repository = document_repository or RequirementDocumentRepository()
        self.revision_repository = revision_repository or RequirementRevisionRepository()

    def create_requirement(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_in: RequirementCreate,
        actor_id: int | None = None,
    ) -> Requirement:
        """要件を作成する。"""
        self._get_document_in_project(
            db,
            project_id=project_id,
            document_id=requirement_in.document_id,
        )
        if (
            self.repository.get_by_document_requirement_code(
                db,
                document_id=requirement_in.document_id,
                requirement_code=requirement_in.requirement_code,
            )
            is not None
        ):
            raise DuplicateResourceError(
                error_messages.REQUIREMENT_CODE_ALREADY_EXISTS
            )

        try:
            return self.repository.create(
                db,
                requirement_in=requirement_in,
                actor_id=actor_id,
            )
        except IntegrityError as exc:
            db.rollback()
            raise DuplicateResourceError(
                error_messages.REQUIREMENT_CODE_ALREADY_EXISTS
            ) from exc

    def list_requirements_paginated(
        self,
        db: Session,
        *,
        project_id: int,
        page: int,
        page_size: int,
        document_id: int | None = None,
        q: str | None = None,
        status: str | None = None,
        requirement_type: str | None = None,
    ) -> tuple[list[Requirement], int]:
        """プロジェクト内の要件一覧をページング付きで取得する。"""
        if document_id is not None:
            self._get_document_in_project(
                db,
                project_id=project_id,
                document_id=document_id,
            )
            document_ids = [document_id]
        else:
            document_ids = self.document_repository.list_ids_by_project(db, project_id)

        if not document_ids:
            return [], 0

        return self.repository.list_paginated(
            db,
            document_ids=document_ids,
            page=page,
            page_size=page_size,
            q=q,
            status=status,
            requirement_type=requirement_type,
        )

    def get_requirement(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
    ) -> Requirement:
        """要件を取得する。"""
        requirement = self.repository.get_by_id(db, requirement_id)
        if requirement is None:
            raise NotFoundError(error_messages.REQUIREMENT_NOT_FOUND)

        self._get_document_in_project(
            db,
            project_id=project_id,
            document_id=requirement.document_id,
        )
        return requirement

    def update_requirement(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        requirement_in: RequirementUpdate,
        actor_id: int | None = None,
    ) -> Requirement:
        """要件を更新し、改訂履歴を作成する。"""
        requirement = self.get_requirement(
            db,
            project_id=project_id,
            requirement_id=requirement_id,
        )
        if requirement.version != requirement_in.version:
            current = RequirementRead.model_validate(requirement).model_dump()
            raise VersionConflictError(current=current)

        if (
            requirement_in.requirement_code is not None
            and requirement_in.requirement_code != requirement.requirement_code
            and self.repository.get_by_document_requirement_code(
                db,
                document_id=requirement.document_id,
                requirement_code=requirement_in.requirement_code,
            )
            is not None
        ):
            raise DuplicateResourceError(
                error_messages.REQUIREMENT_CODE_ALREADY_EXISTS
            )

        before_value = self._build_revision_snapshot(requirement)
        try:
            updated_requirement = self.repository.update(
                db,
                requirement=requirement,
                requirement_in=requirement_in,
                actor_id=actor_id,
            )
            after_value = self._build_revision_snapshot(updated_requirement)
            self.revision_repository.create(
                db,
                requirement_id=updated_requirement.id,
                version=updated_requirement.version,
                changed_by=actor_id,
                change_summary=requirement_in.change_summary,
                before_value=before_value,
                after_value=after_value,
                reason=requirement_in.reason,
            )
            db.commit()
            db.refresh(updated_requirement)
            return updated_requirement
        except IntegrityError as exc:
            db.rollback()
            raise DuplicateResourceError(
                error_messages.REQUIREMENT_CODE_ALREADY_EXISTS
            ) from exc

    def delete_requirement(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        actor_id: int | None = None,
    ) -> None:
        """要件を論理削除する。"""
        requirement = self.get_requirement(
            db,
            project_id=project_id,
            requirement_id=requirement_id,
        )
        self.repository.soft_delete(db, requirement=requirement, actor_id=actor_id)

    def list_revisions(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
    ) -> list[RequirementRevision]:
        """要件の改訂履歴一覧を取得する。"""
        requirement = self.get_requirement(
            db,
            project_id=project_id,
            requirement_id=requirement_id,
        )
        return self.revision_repository.list_by_requirement(db, requirement.id)

    def _get_document_in_project(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
    ) -> RequirementDocument:
        """プロジェクト内の要件定義書を取得する。"""
        document = self.document_repository.get_by_id(db, document_id)
        if document is None or document.project_id != project_id:
            raise NotFoundError(error_messages.REQUIREMENT_DOCUMENT_NOT_FOUND)
        return document

    def _build_revision_snapshot(self, requirement: Requirement) -> dict[str, object]:
        """改訂履歴に保存する要件スナップショットを作成する。"""
        return {
            "id": requirement.id,
            "document_id": requirement.document_id,
            "requirement_code": requirement.requirement_code,
            "requirement_type": requirement.requirement_type,
            "category": requirement.category,
            "title": requirement.title,
            "description": requirement.description,
            "rationale": requirement.rationale,
            "acceptance_criteria": requirement.acceptance_criteria,
            "priority": requirement.priority,
            "status": requirement.status,
            "source": requirement.source,
            "owner_id": requirement.owner_id,
            "approved_by": requirement.approved_by,
            "version": requirement.version,
        }


class RequirementChildService:
    """要件詳細、リンク、コメント、レビューのビジネスロジックを提供する。"""

    def __init__(
        self,
        requirement_service: RequirementService | None = None,
        detail_repository: RequirementDetailRepository | None = None,
        link_repository: RequirementLinkRepository | None = None,
        comment_repository: RequirementCommentRepository | None = None,
        review_repository: RequirementReviewRepository | None = None,
        revision_repository: RequirementRevisionRepository | None = None,
    ) -> None:
        """RequirementChildServiceを初期化する。"""
        self.requirement_service = requirement_service or RequirementService()
        self.detail_repository = detail_repository or RequirementDetailRepository()
        self.link_repository = link_repository or RequirementLinkRepository()
        self.comment_repository = comment_repository or RequirementCommentRepository()
        self.review_repository = review_repository or RequirementReviewRepository()
        self.revision_repository = revision_repository or RequirementRevisionRepository()

    def get_summary(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        latest_limit: int = 20,
    ) -> RequirementSummary:
        """要件詳細画面用の集約情報を取得する。"""
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        return {
            "requirement": requirement,
            "details": self.detail_repository.list_by_requirement(db, requirement_id),
            "links": self.link_repository.list_by_requirement(db, requirement_id),
            "comments": self.comment_repository.list_latest_by_requirement(
                db,
                requirement_id,
                latest_limit,
            ),
            "reviews": self.review_repository.list_by_requirement(db, requirement_id),
            "revisions": self.revision_repository.list_latest_by_requirement(
                db,
                requirement_id,
                latest_limit,
            ),
        }

    def create_detail(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        detail_in: RequirementDetailCreate,
    ) -> RequirementDetail:
        """要件詳細を作成する。"""
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        return self.detail_repository.create(
            db,
            requirement_id=requirement_id,
            detail_in=detail_in,
        )

    def list_details(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
    ) -> list[RequirementDetail]:
        """要件詳細一覧を取得する。"""
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        return self.detail_repository.list_by_requirement(db, requirement_id)

    def update_detail(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        detail_id: int,
        detail_in: RequirementDetailUpdate,
    ) -> RequirementDetail:
        """要件詳細を更新する。"""
        detail = self._get_detail_in_requirement(db, project_id, requirement_id, detail_id)
        return self.detail_repository.update(db, detail=detail, detail_in=detail_in)

    def delete_detail(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        detail_id: int,
    ) -> None:
        """要件詳細を物理削除する。"""
        detail = self._get_detail_in_requirement(db, project_id, requirement_id, detail_id)
        self.detail_repository.delete(db, detail)

    def create_link(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        link_in: RequirementLinkCreate,
    ) -> RequirementLink:
        """要件リンクを作成する。"""
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        return self.link_repository.create(
            db,
            requirement_id=requirement_id,
            link_in=link_in,
        )

    def list_links(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
    ) -> list[RequirementLink]:
        """要件リンク一覧を取得する。"""
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        return self.link_repository.list_by_requirement(db, requirement_id)

    def delete_link(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        link_id: int,
    ) -> None:
        """要件リンクを物理削除する。"""
        link = self._get_link_in_requirement(db, project_id, requirement_id, link_id)
        self.link_repository.delete(db, link)

    def create_comment(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        user_id: int,
        comment_in: RequirementCommentCreate,
    ) -> RequirementComment:
        """要件コメントを作成する。"""
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        return self.comment_repository.create(
            db,
            requirement_id=requirement_id,
            user_id=user_id,
            comment_in=comment_in,
        )

    def list_comments(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
    ) -> list[RequirementComment]:
        """要件コメント一覧を取得する。"""
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        return self.comment_repository.list_by_requirement(db, requirement_id)

    def delete_comment(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        comment_id: int,
    ) -> None:
        """要件コメントを物理削除する。"""
        comment = self._get_comment_in_requirement(
            db,
            project_id,
            requirement_id,
            comment_id,
        )
        self.comment_repository.delete(db, comment)

    def create_review(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        review_in: RequirementReviewCreate,
    ) -> RequirementReview:
        """要件レビューを作成する。"""
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        return self.review_repository.create(
            db,
            requirement_id=requirement_id,
            review_in=review_in,
        )

    def list_reviews(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
    ) -> list[RequirementReview]:
        """要件レビュー一覧を取得する。"""
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        return self.review_repository.list_by_requirement(db, requirement_id)

    def update_review(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        review_id: int,
        review_in: RequirementReviewUpdate,
    ) -> RequirementReview:
        """要件レビューを更新する。"""
        review = self._get_review_in_requirement(db, project_id, requirement_id, review_id)
        return self.review_repository.update(db, review=review, review_in=review_in)

    def delete_review(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        review_id: int,
    ) -> None:
        """要件レビューを物理削除する。"""
        review = self._get_review_in_requirement(db, project_id, requirement_id, review_id)
        self.review_repository.delete(db, review)

    def _ensure_requirement_in_project(
        self,
        db: Session,
        project_id: int,
        requirement_id: int,
    ) -> Requirement:
        """要件が対象プロジェクト配下に存在することを確認する。"""
        return self.requirement_service.get_requirement(
            db,
            project_id=project_id,
            requirement_id=requirement_id,
        )

    def _get_detail_in_requirement(
        self,
        db: Session,
        project_id: int,
        requirement_id: int,
        detail_id: int,
    ) -> RequirementDetail:
        """要件配下の詳細を取得する。"""
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        detail = self.detail_repository.get_by_id(db, detail_id)
        if detail is None or detail.requirement_id != requirement_id:
            raise NotFoundError(error_messages.REQUIREMENT_DETAIL_NOT_FOUND)
        return detail

    def _get_link_in_requirement(
        self,
        db: Session,
        project_id: int,
        requirement_id: int,
        link_id: int,
    ) -> RequirementLink:
        """要件配下のリンクを取得する。"""
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        link = self.link_repository.get_by_id(db, link_id)
        if link is None or link.requirement_id != requirement_id:
            raise NotFoundError(error_messages.REQUIREMENT_LINK_NOT_FOUND)
        return link

    def _get_comment_in_requirement(
        self,
        db: Session,
        project_id: int,
        requirement_id: int,
        comment_id: int,
    ) -> RequirementComment:
        """要件配下のコメントを取得する。"""
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        comment = self.comment_repository.get_by_id(db, comment_id)
        if comment is None or comment.requirement_id != requirement_id:
            raise NotFoundError(error_messages.REQUIREMENT_COMMENT_NOT_FOUND)
        return comment

    def _get_review_in_requirement(
        self,
        db: Session,
        project_id: int,
        requirement_id: int,
        review_id: int,
    ) -> RequirementReview:
        """要件配下のレビューを取得する。"""
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        review = self.review_repository.get_by_id(db, review_id)
        if review is None or review.requirement_id != requirement_id:
            raise NotFoundError(error_messages.REQUIREMENT_REVIEW_NOT_FOUND)
        return review
