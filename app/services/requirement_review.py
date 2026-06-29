"""要件レビューServiceを定義するモジュール。"""

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import NotFoundError
from app.models.requirement import (
    RequirementReview,
)
from app.schemas.requirement import (
    RequirementReviewCreate,
    RequirementReviewUpdate,
)
from app.services.requirement_change_log import (
    RequirementChangeLogAction,
    RequirementChangeLogTargetType,
)
from app.services.requirement_child_base import RequirementChildBaseService


class RequirementReviewService(RequirementChildBaseService):
    """要件レビューServiceを定義するモジュール。"""

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
