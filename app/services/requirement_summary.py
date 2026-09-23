"""要件詳細画面集約Serviceを定義するモジュール。"""

from typing import TypedDict

from sqlalchemy.orm import Session

from app.models.requirement import (
    Requirement,
    RequirementComment,
    RequirementDetail,
    RequirementLink,
    RequirementReview,
    RequirementRevision,
)
from app.services.requirement_child_base import RequirementChildBaseService


class RequirementSummary(TypedDict):
    """要件詳細画面用の集約情報型。"""

    requirement: Requirement
    details: list[RequirementDetail]
    links: list[RequirementLink]
    comments: list[RequirementComment]
    reviews: list[RequirementReview]
    revisions: list[RequirementRevision]


class RequirementSummaryService(RequirementChildBaseService):
    """要件詳細画面集約Serviceを定義するモジュール。"""

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
