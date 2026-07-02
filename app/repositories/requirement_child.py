"""要件定義Repositoryを定義するモジュール。"""

from sqlalchemy.orm import Session

from app.models.requirement import (
    RequirementComment,
    RequirementDetail,
    RequirementLink,
    RequirementRelation,
    RequirementReview,
    RequirementRevision,
)
from app.schemas.requirement import (
    RequirementCommentCreate,
    RequirementDetailCreate,
    RequirementDetailUpdate,
    RequirementLinkCreate,
    RequirementLinkUpdate,
    RequirementRelationCreate,
    RequirementReviewCreate,
    RequirementReviewUpdate,
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


class RequirementRelationRepository:
    """RequirementRelationテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        document_id: int,
        source_requirement_id: int,
        relation_in: RequirementRelationCreate,
        actor_id: int | None = None,
    ) -> RequirementRelation:
        """要件関連を作成する。"""
        relation = RequirementRelation(
            document_id=document_id,
            source_requirement_id=source_requirement_id,
            target_type=relation_in.target_type,
            target_id=relation_in.target_id,
            relation_type=relation_in.relation_type,
            description=relation_in.description,
            created_by=actor_id,
        )
        db.add(relation)
        db.commit()
        db.refresh(relation)
        return relation

    def list_by_requirement(
        self,
        db: Session,
        requirement_id: int,
    ) -> list[RequirementRelation]:
        """要件関連一覧を取得する。"""
        return (
            db.query(RequirementRelation)
            .filter(RequirementRelation.source_requirement_id == requirement_id)
            .order_by(RequirementRelation.id)
            .all()
        )

    def get_by_id(
        self,
        db: Session,
        relation_id: int,
    ) -> RequirementRelation | None:
        """idに一致する要件関連を取得する。"""
        return (
            db.query(RequirementRelation)
            .filter(RequirementRelation.id == relation_id)
            .first()
        )

    def delete(self, db: Session, relation: RequirementRelation) -> None:
        """要件関連を物理削除する。"""
        db.delete(relation)
        db.commit()


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
            linked_url=link_in.linked_url,
            status=link_in.status,
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

    def update(
        self,
        db: Session,
        link: RequirementLink,
        link_in: RequirementLinkUpdate,
    ) -> RequirementLink:
        """要件リンクを更新する。"""
        if link_in.linked_type is not None:
            link.linked_type = link_in.linked_type
        if link_in.linked_id is not None:
            link.linked_id = link_in.linked_id
        if "linked_url" in link_in.model_fields_set:
            link.linked_url = link_in.linked_url
        if link_in.status is not None:
            link.status = link_in.status

        db.commit()
        db.refresh(link)
        return link

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
