"""要件詳細、リンク、コメント、レビューサービスを定義するモジュール。"""

from typing import TypedDict

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import NotFoundError
from app.models.requirement import (
    Requirement,
    RequirementComment,
    RequirementDetail,
    RequirementLink,
    RequirementRelation,
    RequirementReview,
    RequirementRevision,
)
from app.repositories.requirement import (
    RequirementCommentRepository,
    RequirementDetailRepository,
    RequirementLinkRepository,
    RequirementRelationRepository,
    RequirementReviewRepository,
    RequirementRevisionRepository,
)
from app.schemas.requirement import (
    RequirementCommentCreate,
    RequirementDetailCreate,
    RequirementDetailUpdate,
    RequirementLinkCreate,
    RequirementRelationCreate,
    RequirementReviewCreate,
    RequirementReviewUpdate,
)
from app.services.requirement_change_log import (
    RequirementChangeLogAction,
    RequirementChangeLogService,
    RequirementChangeLogTargetType,
)
from app.services.requirement_item import RequirementService


class RequirementSummary(TypedDict):
    """要件詳細画面用の集約情報型。"""

    requirement: Requirement
    details: list[RequirementDetail]
    links: list[RequirementLink]
    comments: list[RequirementComment]
    reviews: list[RequirementReview]
    revisions: list[RequirementRevision]


class RequirementChildService:
    """要件詳細、リンク、コメント、レビューのビジネスロジックを提供する。"""

    def __init__(
        self,
        requirement_service: RequirementService | None = None,
        detail_repository: RequirementDetailRepository | None = None,
        link_repository: RequirementLinkRepository | None = None,
        relation_repository: RequirementRelationRepository | None = None,
        comment_repository: RequirementCommentRepository | None = None,
        review_repository: RequirementReviewRepository | None = None,
        revision_repository: RequirementRevisionRepository | None = None,
        change_log_service: RequirementChangeLogService | None = None,
    ) -> None:
        """RequirementChildServiceを初期化する。

        Args:
            requirement_service: 要件サービス。
            detail_repository: 要件詳細Repository。
            link_repository: 要件リンクRepository。
            relation_repository: 要件関連Repository。
            comment_repository: 要件コメントRepository。
            review_repository: 要件レビューRepository。
            revision_repository: 要件改訂履歴Repository。
            change_log_service: 要件定義変更履歴Service。
        """
        self.requirement_service = requirement_service or RequirementService()
        self.detail_repository = detail_repository or RequirementDetailRepository()
        self.link_repository = link_repository or RequirementLinkRepository()
        self.relation_repository = (
            relation_repository or RequirementRelationRepository()
        )
        self.comment_repository = comment_repository or RequirementCommentRepository()
        self.review_repository = review_repository or RequirementReviewRepository()
        self.revision_repository = (
            revision_repository or RequirementRevisionRepository()
        )
        self.change_log_service = change_log_service or RequirementChangeLogService()

    def get_summary(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        latest_limit: int = 20,
    ) -> RequirementSummary:
        """要件詳細画面用の集約情報を取得する。

        Args:
            db: DBセッション。
            project_id: 取得対象のプロジェクトID。
            requirement_id: 取得対象の要件ID。
            latest_limit: コメントと改訂履歴の最大取得件数。

        Returns:
            要件、詳細、リンク、コメント、レビュー、改訂履歴の集約情報。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
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
        actor_id: int | None = None,
    ) -> RequirementDetail:
        """要件詳細を作成する。

        Args:
            db: DBセッション。
            project_id: 作成対象のプロジェクトID。
            requirement_id: 作成対象の要件ID。
            detail_in: 要件詳細の作成入力値。
            actor_id: 操作ユーザーID。

        Returns:
            作成された要件詳細。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        detail = self.detail_repository.create(
            db,
            requirement_id=requirement_id,
            detail_in=detail_in,
        )
        self._record_child_change_log(
            db,
            requirement=requirement,
            target_type=RequirementChangeLogTargetType.DETAIL,
            target_id=detail.id,
            action=RequirementChangeLogAction.CREATED,
            new_value=self._build_detail_snapshot(detail),
            changed_by=actor_id,
        )
        return detail

    def list_details(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
    ) -> list[RequirementDetail]:
        """要件詳細一覧を取得する。

        Args:
            db: DBセッション。
            project_id: 取得対象のプロジェクトID。
            requirement_id: 取得対象の要件ID。

        Returns:
            要件詳細一覧。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
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
        actor_id: int | None = None,
    ) -> RequirementDetail:
        """要件詳細を更新する。

        Args:
            db: DBセッション。
            project_id: 更新対象のプロジェクトID。
            requirement_id: 更新対象の要件ID。
            detail_id: 更新対象の要件詳細ID。
            detail_in: 要件詳細の更新入力値。
            actor_id: 操作ユーザーID。

        Returns:
            更新された要件詳細。

        Raises:
            NotFoundError: 要件詳細が存在しない、または要件に属さない場合。
        """
        detail = self._get_detail_in_requirement(
            db,
            project_id,
            requirement_id,
            detail_id,
        )
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        before_value = self._build_detail_snapshot(detail)
        updated_detail = self.detail_repository.update(
            db,
            detail=detail,
            detail_in=detail_in,
        )
        self._record_child_change_log(
            db,
            requirement=requirement,
            target_type=RequirementChangeLogTargetType.DETAIL,
            target_id=updated_detail.id,
            action=RequirementChangeLogAction.UPDATED,
            old_value=before_value,
            new_value=self._build_detail_snapshot(updated_detail),
            changed_by=actor_id,
        )
        return updated_detail

    def delete_detail(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        detail_id: int,
        actor_id: int | None = None,
    ) -> None:
        """要件詳細を物理削除する。

        Args:
            db: DBセッション。
            project_id: 削除対象のプロジェクトID。
            requirement_id: 削除対象の要件ID。
            detail_id: 削除対象の要件詳細ID。
            actor_id: 操作ユーザーID。

        Raises:
            NotFoundError: 要件詳細が存在しない、または要件に属さない場合。
        """
        detail = self._get_detail_in_requirement(
            db,
            project_id,
            requirement_id,
            detail_id,
        )
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        before_value = self._build_detail_snapshot(detail)
        self.detail_repository.delete(db, detail)
        self._record_child_change_log(
            db,
            requirement=requirement,
            target_type=RequirementChangeLogTargetType.DETAIL,
            target_id=detail_id,
            action=RequirementChangeLogAction.DELETED,
            old_value=before_value,
            changed_by=actor_id,
        )

    def create_link(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        link_in: RequirementLinkCreate,
        actor_id: int | None = None,
    ) -> RequirementLink:
        """要件リンクを作成する。

        Args:
            db: DBセッション。
            project_id: 作成対象のプロジェクトID。
            requirement_id: 作成対象の要件ID。
            link_in: 要件リンクの作成入力値。
            actor_id: 操作ユーザーID。

        Returns:
            作成された要件リンク。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        link = self.link_repository.create(
            db,
            requirement_id=requirement_id,
            link_in=link_in,
        )
        self._record_child_change_log(
            db,
            requirement=requirement,
            target_type=RequirementChangeLogTargetType.LINK,
            target_id=link.id,
            action=RequirementChangeLogAction.CREATED,
            new_value=self._build_link_snapshot(link),
            changed_by=actor_id,
        )
        return link

    def list_links(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
    ) -> list[RequirementLink]:
        """要件リンク一覧を取得する。

        Args:
            db: DBセッション。
            project_id: 取得対象のプロジェクトID。
            requirement_id: 取得対象の要件ID。

        Returns:
            要件リンク一覧。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        return self.link_repository.list_by_requirement(db, requirement_id)

    def delete_link(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        link_id: int,
        actor_id: int | None = None,
    ) -> None:
        """要件リンクを物理削除する。

        Args:
            db: DBセッション。
            project_id: 削除対象のプロジェクトID。
            requirement_id: 削除対象の要件ID。
            link_id: 削除対象の要件リンクID。
            actor_id: 操作ユーザーID。

        Raises:
            NotFoundError: 要件リンクが存在しない、または要件に属さない場合。
        """
        link = self._get_link_in_requirement(db, project_id, requirement_id, link_id)
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        before_value = self._build_link_snapshot(link)
        self.link_repository.delete(db, link)
        self._record_child_change_log(
            db,
            requirement=requirement,
            target_type=RequirementChangeLogTargetType.LINK,
            target_id=link_id,
            action=RequirementChangeLogAction.DELETED,
            old_value=before_value,
            changed_by=actor_id,
        )

    def create_relation(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        relation_in: RequirementRelationCreate,
        actor_id: int | None = None,
    ) -> RequirementRelation:
        """要件関連を作成する。

        Args:
            db: DBセッション。
            project_id: 作成対象のプロジェクトID。
            requirement_id: 関連元の要件ID。
            relation_in: 要件関連の作成入力値。
            actor_id: 操作ユーザーID。

        Returns:
            作成された要件関連。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        relation = self.relation_repository.create(
            db,
            document_id=requirement.document_id,
            source_requirement_id=requirement.id,
            relation_in=relation_in,
            actor_id=actor_id,
        )
        self._record_child_change_log(
            db,
            requirement=requirement,
            target_type=RequirementChangeLogTargetType.RELATION,
            target_id=relation.id,
            action=RequirementChangeLogAction.CREATED,
            new_value=self._build_relation_snapshot(relation),
            changed_by=actor_id,
        )
        return relation

    def list_relations(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
    ) -> list[RequirementRelation]:
        """要件関連一覧を取得する。"""
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        return self.relation_repository.list_by_requirement(db, requirement_id)

    def delete_relation(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        relation_id: int,
        actor_id: int | None = None,
    ) -> None:
        """要件関連を物理削除する。"""
        relation = self._get_relation_in_requirement(
            db,
            project_id,
            requirement_id,
            relation_id,
        )
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        before_value = self._build_relation_snapshot(relation)
        self.relation_repository.delete(db, relation)
        self._record_child_change_log(
            db,
            requirement=requirement,
            target_type=RequirementChangeLogTargetType.RELATION,
            target_id=relation_id,
            action=RequirementChangeLogAction.DELETED,
            old_value=before_value,
            changed_by=actor_id,
        )

    def create_comment(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        user_id: int,
        comment_in: RequirementCommentCreate,
    ) -> RequirementComment:
        """要件コメントを作成する。

        Args:
            db: DBセッション。
            project_id: 作成対象のプロジェクトID。
            requirement_id: 作成対象の要件ID。
            user_id: コメント作成ユーザーID。
            comment_in: 要件コメントの作成入力値。

        Returns:
            作成された要件コメント。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
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
        """要件コメント一覧を取得する。

        Args:
            db: DBセッション。
            project_id: 取得対象のプロジェクトID。
            requirement_id: 取得対象の要件ID。

        Returns:
            要件コメント一覧。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
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
        """要件コメントを物理削除する。

        Args:
            db: DBセッション。
            project_id: 削除対象のプロジェクトID。
            requirement_id: 削除対象の要件ID。
            comment_id: 削除対象の要件コメントID。

        Raises:
            NotFoundError: 要件コメントが存在しない、または要件に属さない場合。
        """
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
        actor_id: int | None = None,
    ) -> RequirementReview:
        """要件レビューを作成する。

        Args:
            db: DBセッション。
            project_id: 作成対象のプロジェクトID。
            requirement_id: 作成対象の要件ID。
            review_in: 要件レビューの作成入力値。
            actor_id: 操作ユーザーID。

        Returns:
            作成された要件レビュー。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        review = self.review_repository.create(
            db,
            requirement_id=requirement_id,
            review_in=review_in,
        )
        self._record_child_change_log(
            db,
            requirement=requirement,
            target_type=RequirementChangeLogTargetType.REVIEW,
            target_id=review.id,
            action=RequirementChangeLogAction.CREATED,
            new_value=self._build_review_snapshot(review),
            changed_by=actor_id,
        )
        return review

    def list_reviews(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
    ) -> list[RequirementReview]:
        """要件レビュー一覧を取得する。

        Args:
            db: DBセッション。
            project_id: 取得対象のプロジェクトID。
            requirement_id: 取得対象の要件ID。

        Returns:
            要件レビュー一覧。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
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
        actor_id: int | None = None,
    ) -> RequirementReview:
        """要件レビューを更新する。

        Args:
            db: DBセッション。
            project_id: 更新対象のプロジェクトID。
            requirement_id: 更新対象の要件ID。
            review_id: 更新対象の要件レビューID。
            review_in: 要件レビューの更新入力値。
            actor_id: 操作ユーザーID。

        Returns:
            更新された要件レビュー。

        Raises:
            NotFoundError: 要件レビューが存在しない、または要件に属さない場合。
        """
        review = self._get_review_in_requirement(
            db,
            project_id,
            requirement_id,
            review_id,
        )
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        before_value = self._build_review_snapshot(review)
        updated_review = self.review_repository.update(
            db,
            review=review,
            review_in=review_in,
        )
        self._record_child_change_log(
            db,
            requirement=requirement,
            target_type=RequirementChangeLogTargetType.REVIEW,
            target_id=updated_review.id,
            action=RequirementChangeLogAction.UPDATED,
            old_value=before_value,
            new_value=self._build_review_snapshot(updated_review),
            changed_by=actor_id,
        )
        return updated_review

    def delete_review(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        review_id: int,
        actor_id: int | None = None,
    ) -> None:
        """要件レビューを物理削除する。

        Args:
            db: DBセッション。
            project_id: 削除対象のプロジェクトID。
            requirement_id: 削除対象の要件ID。
            review_id: 削除対象の要件レビューID。
            actor_id: 操作ユーザーID。

        Raises:
            NotFoundError: 要件レビューが存在しない、または要件に属さない場合。
        """
        review = self._get_review_in_requirement(
            db,
            project_id,
            requirement_id,
            review_id,
        )
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        before_value = self._build_review_snapshot(review)
        self.review_repository.delete(db, review)
        self._record_child_change_log(
            db,
            requirement=requirement,
            target_type=RequirementChangeLogTargetType.REVIEW,
            target_id=review_id,
            action=RequirementChangeLogAction.DELETED,
            old_value=before_value,
            changed_by=actor_id,
        )

    def _ensure_requirement_in_project(
        self,
        db: Session,
        project_id: int,
        requirement_id: int,
    ) -> Requirement:
        """要件が対象プロジェクト配下に存在することを確認する。

        Args:
            db: DBセッション。
            project_id: 所属確認対象のプロジェクトID。
            requirement_id: 所属確認対象の要件ID。

        Returns:
            取得した要件。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
        return self.requirement_service.get_requirement(
            db,
            project_id=project_id,
            requirement_id=requirement_id,
        )

    def _record_child_change_log(
        self,
        db: Session,
        *,
        requirement: Requirement,
        target_type: str,
        target_id: int,
        action: str,
        old_value: dict[str, object] | None = None,
        new_value: dict[str, object] | None = None,
        changed_by: int | None = None,
    ) -> None:
        """要件子リソースの変更履歴を記録する。"""
        self.change_log_service.record(
            db,
            document_id=requirement.document_id,
            target_type=target_type,
            target_id=target_id,
            action=action,
            old_value=old_value,
            new_value=new_value,
            changed_by=changed_by,
        )

    def _build_detail_snapshot(
        self,
        detail: RequirementDetail,
    ) -> dict[str, object]:
        """変更履歴に保存する要件詳細スナップショットを作成する。"""
        return {
            "id": detail.id,
            "requirement_id": detail.requirement_id,
            "detail_type": detail.detail_type,
            "detail_json": detail.detail_json,
        }

    def _build_link_snapshot(
        self,
        link: RequirementLink,
    ) -> dict[str, object]:
        """変更履歴に保存する要件リンクスナップショットを作成する。"""
        return {
            "id": link.id,
            "requirement_id": link.requirement_id,
            "linked_type": link.linked_type,
            "linked_id": link.linked_id,
        }

    def _build_relation_snapshot(
        self,
        relation: RequirementRelation,
    ) -> dict[str, object]:
        """変更履歴に保存する要件関連スナップショットを作成する。"""
        return {
            "id": relation.id,
            "document_id": relation.document_id,
            "source_requirement_id": relation.source_requirement_id,
            "target_type": relation.target_type,
            "target_id": relation.target_id,
            "relation_type": relation.relation_type,
            "description": relation.description,
            "created_by": relation.created_by,
        }

    def _build_review_snapshot(
        self,
        review: RequirementReview,
    ) -> dict[str, object]:
        """変更履歴に保存する要件レビュースナップショットを作成する。"""
        return {
            "id": review.id,
            "requirement_id": review.requirement_id,
            "reviewer_id": review.reviewer_id,
            "status": review.status,
            "comment": review.comment,
            "reviewed_at": review.reviewed_at.isoformat()
            if review.reviewed_at is not None
            else None,
        }

    def _get_detail_in_requirement(
        self,
        db: Session,
        project_id: int,
        requirement_id: int,
        detail_id: int,
    ) -> RequirementDetail:
        """要件配下の詳細を取得する。

        Args:
            db: DBセッション。
            project_id: 所属確認対象のプロジェクトID。
            requirement_id: 所属確認対象の要件ID。
            detail_id: 取得対象の要件詳細ID。

        Returns:
            取得した要件詳細。

        Raises:
            NotFoundError: 要件詳細が存在しない、または要件に属さない場合。
        """
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
        """要件配下のリンクを取得する。

        Args:
            db: DBセッション。
            project_id: 所属確認対象のプロジェクトID。
            requirement_id: 所属確認対象の要件ID。
            link_id: 取得対象の要件リンクID。

        Returns:
            取得した要件リンク。

        Raises:
            NotFoundError: 要件リンクが存在しない、または要件に属さない場合。
        """
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        link = self.link_repository.get_by_id(db, link_id)
        if link is None or link.requirement_id != requirement_id:
            raise NotFoundError(error_messages.REQUIREMENT_LINK_NOT_FOUND)
        return link

    def _get_relation_in_requirement(
        self,
        db: Session,
        project_id: int,
        requirement_id: int,
        relation_id: int,
    ) -> RequirementRelation:
        """要件配下の関連を取得する。"""
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        relation = self.relation_repository.get_by_id(db, relation_id)
        if relation is None or relation.source_requirement_id != requirement_id:
            raise NotFoundError(error_messages.REQUIREMENT_RELATION_NOT_FOUND)
        return relation

    def _get_comment_in_requirement(
        self,
        db: Session,
        project_id: int,
        requirement_id: int,
        comment_id: int,
    ) -> RequirementComment:
        """要件配下のコメントを取得する。

        Args:
            db: DBセッション。
            project_id: 所属確認対象のプロジェクトID。
            requirement_id: 所属確認対象の要件ID。
            comment_id: 取得対象の要件コメントID。

        Returns:
            取得した要件コメント。

        Raises:
            NotFoundError: 要件コメントが存在しない、または要件に属さない場合。
        """
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
        """要件配下のレビューを取得する。

        Args:
            db: DBセッション。
            project_id: 所属確認対象のプロジェクトID。
            requirement_id: 所属確認対象の要件ID。
            review_id: 取得対象の要件レビューID。

        Returns:
            取得した要件レビュー。

        Raises:
            NotFoundError: 要件レビューが存在しない、または要件に属さない場合。
        """
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        review = self.review_repository.get_by_id(db, review_id)
        if review is None or review.requirement_id != requirement_id:
            raise NotFoundError(error_messages.REQUIREMENT_REVIEW_NOT_FOUND)
        return review
