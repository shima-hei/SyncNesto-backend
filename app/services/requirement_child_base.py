"""要件子リソースServiceの共通処理を定義するモジュール。"""

from typing import TypedDict

from sqlalchemy.orm import Session

from app.models.requirement import (
    Requirement,
    RequirementComment,
    RequirementDetail,
    RequirementLink,
    RequirementRelation,
    RequirementReview,
    RequirementRevision,
)
from app.repositories.requirement_child import (
    RequirementCommentRepository,
    RequirementDetailRepository,
    RequirementLinkRepository,
    RequirementRelationRepository,
    RequirementReviewRepository,
    RequirementRevisionRepository,
)
from app.repositories.user import UserRepository
from app.services.requirement_change_log import (
    RequirementChangeLogService,
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


class RequirementChildBaseService:
    """要件子リソースServiceの共通処理を提供する。"""

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
        user_repository: UserRepository | None = None,
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
            user_repository: ユーザーRepository。
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
        self.user_repository = user_repository or UserRepository()

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
            "linked_url": link.linked_url,
            "status": link.status,
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
