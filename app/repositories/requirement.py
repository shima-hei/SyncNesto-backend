"""要件定義Repositoryを定義するモジュール。"""

from datetime import UTC, datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.requirement import (
    Requirement,
    RequirementComment,
    RequirementDetail,
    RequirementDocument,
    RequirementLink,
    RequirementReview,
    RequirementRevision,
)
from app.schemas.requirement import (
    RequirementCommentCreate,
    RequirementCreate,
    RequirementDetailCreate,
    RequirementDetailUpdate,
    RequirementDocumentCreate,
    RequirementDocumentUpdate,
    RequirementLinkCreate,
    RequirementReviewCreate,
    RequirementReviewUpdate,
    RequirementUpdate,
)


class RequirementDocumentRepository:
    """RequirementDocumentテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        project_id: int,
        document_in: RequirementDocumentCreate,
        actor_id: int | None = None,
    ) -> RequirementDocument:
        """要件定義書を作成する。"""
        document = RequirementDocument(
            project_id=project_id,
            title=document_in.title,
            document_code=document_in.document_code,
            status=document_in.status,
            purpose=document_in.purpose,
            target_system_name=document_in.target_system_name,
            client_name=document_in.client_name,
            vendor_name=document_in.vendor_name,
            author_id=document_in.author_id,
            reviewer_id=document_in.reviewer_id,
            approver_id=document_in.approver_id,
            approved_at=document_in.approved_at,
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        return document

    def get_by_id(
        self,
        db: Session,
        document_id: int,
    ) -> RequirementDocument | None:
        """idに一致する要件定義書を取得する。"""
        return (
            db.query(RequirementDocument)
            .filter(
                RequirementDocument.id == document_id,
                RequirementDocument.deleted_at.is_(None),
            )
            .first()
        )

    def get_by_project_document_code(
        self,
        db: Session,
        *,
        project_id: int,
        document_code: str,
    ) -> RequirementDocument | None:
        """project_id/document_codeに一致する要件定義書を取得する。"""
        return (
            db.query(RequirementDocument)
            .filter(
                RequirementDocument.project_id == project_id,
                RequirementDocument.document_code == document_code,
                RequirementDocument.deleted_at.is_(None),
            )
            .first()
        )

    def list_paginated(
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
        query = db.query(RequirementDocument).filter(
            RequirementDocument.project_id == project_id,
            RequirementDocument.deleted_at.is_(None),
        )
        if q:
            like_pattern = f"%{q}%"
            query = query.filter(
                or_(
                    RequirementDocument.document_code.ilike(like_pattern),
                    RequirementDocument.title.ilike(like_pattern),
                    RequirementDocument.purpose.ilike(like_pattern),
                )
            )
        if status is not None:
            query = query.filter(RequirementDocument.status == status)

        total = query.count()
        documents = (
            query.order_by(RequirementDocument.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return documents, total

    def list_ids_by_project(self, db: Session, project_id: int) -> list[int]:
        """プロジェクト内の要件定義書ID一覧を取得する。"""
        rows = (
            db.query(RequirementDocument.id)
            .filter(
                RequirementDocument.project_id == project_id,
                RequirementDocument.deleted_at.is_(None),
            )
            .order_by(RequirementDocument.id)
            .all()
        )
        return [row[0] for row in rows]

    def update(
        self,
        db: Session,
        *,
        document: RequirementDocument,
        document_in: RequirementDocumentUpdate,
        actor_id: int | None = None,
    ) -> RequirementDocument:
        """要件定義書を更新する。"""
        for field in (
            "title",
            "document_code",
            "status",
            "purpose",
            "target_system_name",
            "client_name",
            "vendor_name",
            "author_id",
            "reviewer_id",
            "approver_id",
            "approved_at",
        ):
            if field in document_in.model_fields_set:
                setattr(document, field, getattr(document_in, field))
        if actor_id is not None:
            document.updated_by = actor_id
        document.version += 1

        db.commit()
        db.refresh(document)
        return document

    def soft_delete(
        self,
        db: Session,
        *,
        document: RequirementDocument,
        actor_id: int | None = None,
    ) -> RequirementDocument:
        """要件定義書を論理削除する。"""
        document.deleted_at = datetime.now(UTC)
        if actor_id is not None:
            document.updated_by = actor_id
        db.commit()
        db.refresh(document)
        return document


class RequirementRepository:
    """Requirementテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        requirement_in: RequirementCreate,
        actor_id: int | None = None,
    ) -> Requirement:
        """要件を作成する。"""
        requirement = Requirement(
            document_id=requirement_in.document_id,
            requirement_code=requirement_in.requirement_code,
            requirement_type=requirement_in.requirement_type,
            category=requirement_in.category,
            title=requirement_in.title,
            description=requirement_in.description,
            rationale=requirement_in.rationale,
            acceptance_criteria=requirement_in.acceptance_criteria,
            priority=requirement_in.priority,
            status=requirement_in.status,
            source=requirement_in.source,
            owner_id=requirement_in.owner_id,
            approved_by=requirement_in.approved_by,
            approved_at=requirement_in.approved_at,
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(requirement)
        db.commit()
        db.refresh(requirement)
        return requirement

    def get_by_id(self, db: Session, requirement_id: int) -> Requirement | None:
        """idに一致する要件を取得する。"""
        return (
            db.query(Requirement)
            .filter(Requirement.id == requirement_id, Requirement.deleted_at.is_(None))
            .first()
        )

    def get_by_document_requirement_code(
        self,
        db: Session,
        *,
        document_id: int,
        requirement_code: str,
    ) -> Requirement | None:
        """document_id/requirement_codeに一致する要件を取得する。"""
        return (
            db.query(Requirement)
            .filter(
                Requirement.document_id == document_id,
                Requirement.requirement_code == requirement_code,
                Requirement.deleted_at.is_(None),
            )
            .first()
        )

    def list_paginated(
        self,
        db: Session,
        *,
        document_ids: list[int],
        page: int,
        page_size: int,
        q: str | None = None,
        status: str | None = None,
        requirement_type: str | None = None,
    ) -> tuple[list[Requirement], int]:
        """指定要件定義書群の要件一覧をページング付きで取得する。"""
        query = db.query(Requirement).filter(
            Requirement.document_id.in_(document_ids),
            Requirement.deleted_at.is_(None),
        )
        if q:
            like_pattern = f"%{q}%"
            query = query.filter(
                or_(
                    Requirement.requirement_code.ilike(like_pattern),
                    Requirement.title.ilike(like_pattern),
                    Requirement.description.ilike(like_pattern),
                )
            )
        if status is not None:
            query = query.filter(Requirement.status == status)
        if requirement_type is not None:
            query = query.filter(Requirement.requirement_type == requirement_type)

        total = query.count()
        requirements = (
            query.order_by(Requirement.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return requirements, total

    def update(
        self,
        db: Session,
        *,
        requirement: Requirement,
        requirement_in: RequirementUpdate,
        actor_id: int | None = None,
    ) -> Requirement:
        """要件を更新する。"""
        for field in (
            "requirement_code",
            "requirement_type",
            "category",
            "title",
            "description",
            "rationale",
            "acceptance_criteria",
            "priority",
            "status",
            "source",
            "owner_id",
            "approved_by",
            "approved_at",
        ):
            if field in requirement_in.model_fields_set:
                setattr(requirement, field, getattr(requirement_in, field))
        if actor_id is not None:
            requirement.updated_by = actor_id
        requirement.version += 1

        db.flush()
        return requirement

    def soft_delete(
        self,
        db: Session,
        *,
        requirement: Requirement,
        actor_id: int | None = None,
    ) -> Requirement:
        """要件を論理削除する。"""
        requirement.deleted_at = datetime.now(UTC)
        if actor_id is not None:
            requirement.updated_by = actor_id
        db.commit()
        db.refresh(requirement)
        return requirement

    def list_by_document_ids(
        self,
        db: Session,
        document_ids: list[int],
    ) -> list[Requirement]:
        """指定要件定義書群の要件一覧を取得する。"""
        if not document_ids:
            return []
        return (
            db.query(Requirement)
            .filter(
                Requirement.document_id.in_(document_ids),
                Requirement.deleted_at.is_(None),
            )
            .order_by(Requirement.id)
            .all()
        )


class RequirementRevisionRepository:
    """RequirementRevisionテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        requirement_id: int,
        version: int,
        changed_by: int | None,
        before_value: dict | None,
        after_value: dict | None,
        change_summary: str | None = None,
        reason: str | None = None,
    ) -> RequirementRevision:
        """要件改訂履歴を作成する。"""
        revision = RequirementRevision(
            requirement_id=requirement_id,
            version=version,
            changed_by=changed_by,
            change_summary=change_summary,
            before_value=before_value,
            after_value=after_value,
            reason=reason,
        )
        db.add(revision)
        db.flush()
        return revision

    def list_by_requirement(
        self,
        db: Session,
        requirement_id: int,
    ) -> list[RequirementRevision]:
        """要件の改訂履歴一覧を取得する。"""
        return (
            db.query(RequirementRevision)
            .filter(RequirementRevision.requirement_id == requirement_id)
            .order_by(RequirementRevision.id)
            .all()
        )

    def list_latest_by_requirement(
        self,
        db: Session,
        requirement_id: int,
        limit: int,
    ) -> list[RequirementRevision]:
        """要件の直近改訂履歴一覧を取得する。"""
        revisions = (
            db.query(RequirementRevision)
            .filter(RequirementRevision.requirement_id == requirement_id)
            .order_by(RequirementRevision.id.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(revisions))


class RequirementDetailRepository:
    """RequirementDetailテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        requirement_id: int,
        detail_in: RequirementDetailCreate,
    ) -> RequirementDetail:
        """要件詳細を作成する。"""
        detail = RequirementDetail(
            requirement_id=requirement_id,
            detail_type=detail_in.detail_type,
            detail_json=detail_in.detail_json,
        )
        db.add(detail)
        db.commit()
        db.refresh(detail)
        return detail

    def list_by_requirement(
        self,
        db: Session,
        requirement_id: int,
    ) -> list[RequirementDetail]:
        """要件詳細一覧を取得する。"""
        return (
            db.query(RequirementDetail)
            .filter(RequirementDetail.requirement_id == requirement_id)
            .order_by(RequirementDetail.id)
            .all()
        )

    def get_by_id(self, db: Session, detail_id: int) -> RequirementDetail | None:
        """idに一致する要件詳細を取得する。"""
        return (
            db.query(RequirementDetail)
            .filter(RequirementDetail.id == detail_id)
            .first()
        )

    def update(
        self,
        db: Session,
        *,
        detail: RequirementDetail,
        detail_in: RequirementDetailUpdate,
    ) -> RequirementDetail:
        """要件詳細を更新する。"""
        if detail_in.detail_type is not None:
            detail.detail_type = detail_in.detail_type
        if detail_in.detail_json is not None:
            detail.detail_json = detail_in.detail_json
        db.commit()
        db.refresh(detail)
        return detail

    def delete(self, db: Session, detail: RequirementDetail) -> None:
        """要件詳細を物理削除する。"""
        db.delete(detail)
        db.commit()


class RequirementLinkRepository:
    """RequirementLinkテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        requirement_id: int,
        link_in: RequirementLinkCreate,
    ) -> RequirementLink:
        """要件リンクを作成する。"""
        link = RequirementLink(
            requirement_id=requirement_id,
            linked_type=link_in.linked_type,
            linked_id=link_in.linked_id,
        )
        db.add(link)
        db.commit()
        db.refresh(link)
        return link

    def list_by_requirement(
        self,
        db: Session,
        requirement_id: int,
    ) -> list[RequirementLink]:
        """要件リンク一覧を取得する。"""
        return (
            db.query(RequirementLink)
            .filter(RequirementLink.requirement_id == requirement_id)
            .order_by(RequirementLink.id)
            .all()
        )

    def get_by_id(self, db: Session, link_id: int) -> RequirementLink | None:
        """idに一致する要件リンクを取得する。"""
        return db.query(RequirementLink).filter(RequirementLink.id == link_id).first()

    def delete(self, db: Session, link: RequirementLink) -> None:
        """要件リンクを物理削除する。"""
        db.delete(link)
        db.commit()


class RequirementCommentRepository:
    """RequirementCommentテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        requirement_id: int,
        user_id: int,
        comment_in: RequirementCommentCreate,
    ) -> RequirementComment:
        """要件コメントを作成する。"""
        comment = RequirementComment(
            requirement_id=requirement_id,
            user_id=user_id,
            comment=comment_in.comment,
        )
        db.add(comment)
        db.commit()
        db.refresh(comment)
        return comment

    def list_by_requirement(
        self,
        db: Session,
        requirement_id: int,
    ) -> list[RequirementComment]:
        """要件コメント一覧を取得する。"""
        return (
            db.query(RequirementComment)
            .filter(RequirementComment.requirement_id == requirement_id)
            .order_by(RequirementComment.id)
            .all()
        )

    def list_latest_by_requirement(
        self,
        db: Session,
        requirement_id: int,
        limit: int,
    ) -> list[RequirementComment]:
        """要件の直近コメント一覧を取得する。"""
        comments = (
            db.query(RequirementComment)
            .filter(RequirementComment.requirement_id == requirement_id)
            .order_by(RequirementComment.id.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(comments))

    def get_by_id(self, db: Session, comment_id: int) -> RequirementComment | None:
        """idに一致する要件コメントを取得する。"""
        return (
            db.query(RequirementComment)
            .filter(RequirementComment.id == comment_id)
            .first()
        )

    def delete(self, db: Session, comment: RequirementComment) -> None:
        """要件コメントを物理削除する。"""
        db.delete(comment)
        db.commit()


class RequirementReviewRepository:
    """RequirementReviewテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        requirement_id: int,
        review_in: RequirementReviewCreate,
    ) -> RequirementReview:
        """要件レビューを作成する。"""
        review = RequirementReview(
            requirement_id=requirement_id,
            reviewer_id=review_in.reviewer_id,
            status=review_in.status,
            comment=review_in.comment,
            reviewed_at=review_in.reviewed_at,
        )
        db.add(review)
        db.commit()
        db.refresh(review)
        return review

    def list_by_requirement(
        self,
        db: Session,
        requirement_id: int,
    ) -> list[RequirementReview]:
        """要件レビュー一覧を取得する。"""
        return (
            db.query(RequirementReview)
            .filter(RequirementReview.requirement_id == requirement_id)
            .order_by(RequirementReview.id)
            .all()
        )

    def get_by_id(self, db: Session, review_id: int) -> RequirementReview | None:
        """idに一致する要件レビューを取得する。"""
        return (
            db.query(RequirementReview)
            .filter(RequirementReview.id == review_id)
            .first()
        )

    def update(
        self,
        db: Session,
        *,
        review: RequirementReview,
        review_in: RequirementReviewUpdate,
    ) -> RequirementReview:
        """要件レビューを更新する。"""
        for field in ("reviewer_id", "status", "comment", "reviewed_at"):
            if field in review_in.model_fields_set:
                setattr(review, field, getattr(review_in, field))
        db.commit()
        db.refresh(review)
        return review

    def delete(self, db: Session, review: RequirementReview) -> None:
        """要件レビューを物理削除する。"""
        db.delete(review)
        db.commit()
