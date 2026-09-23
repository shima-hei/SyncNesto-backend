"""要件詳細コメントの対象アンカー検証を定義するモジュール。"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import NotFoundError
from app.models.requirement import (
    RequirementDetail,
    RequirementLink,
    RequirementRelation,
)
from app.models.task import RequirementTaskRelation
from app.repositories.requirement_child import (
    RequirementDetailRepository,
    RequirementLinkRepository,
    RequirementRelationRepository,
)
from app.repositories.task_item import TaskRepository
from app.repositories.task_requirement import RequirementTaskRelationRepository


class RequirementCommentTargetType:
    """要件定義対象コメントの対象種別定数。"""

    DOCUMENT = "document"
    OPEN_ISSUE = "open_issue"
    REQUIREMENT_ITEM = "requirement_item"
    SECTION = "section"


class RequirementCommentAnchorKind:
    """要件詳細コメントのアンカー種別定数。"""

    REQUIREMENT_FIELD = "requirement_field"
    REQUIREMENT_DETAIL = "requirement_detail"
    REQUIREMENT_LINK = "requirement_link"
    REQUIREMENT_RELATION = "requirement_relation"
    REQUIREMENT_TASK = "requirement_task"


@dataclass(frozen=True)
class CommentTarget:
    """コメント対象の解決結果。"""

    document_id: int
    target_type: str
    target_id: int


class RequirementCommentAnchorValidator:
    """要件詳細コメントのアンカー参照先を検証する。"""

    def __init__(
        self,
        detail_repository: RequirementDetailRepository | None = None,
        link_repository: RequirementLinkRepository | None = None,
        relation_repository: RequirementRelationRepository | None = None,
        task_relation_repository: RequirementTaskRelationRepository | None = None,
        task_repository: TaskRepository | None = None,
    ) -> None:
        """RequirementCommentAnchorValidatorを初期化する。

        Args:
            detail_repository: 要件詳細Repository。
            link_repository: 要件リンクRepository。
            relation_repository: 要件関連Repository。
            task_relation_repository: 要件タスク関連Repository。
            task_repository: タスクRepository。
        """
        self.detail_repository = detail_repository or RequirementDetailRepository()
        self.link_repository = link_repository or RequirementLinkRepository()
        self.relation_repository = (
            relation_repository or RequirementRelationRepository()
        )
        self.task_relation_repository = (
            task_relation_repository or RequirementTaskRelationRepository()
        )
        self.task_repository = task_repository or TaskRepository()

    def validate(
        self,
        db: Session,
        *,
        project_id: int,
        target: CommentTarget,
        target_anchor: dict | None,
    ) -> None:
        """コメント対象内アンカーの参照先が存在することを確認する。"""
        if not target_anchor:
            return

        kind = target_anchor.get("kind")
        if kind is None:
            return
        if not isinstance(kind, str):
            raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)

        if kind == RequirementCommentAnchorKind.REQUIREMENT_FIELD:
            self._ensure_anchor_target_is_requirement(target)
            return
        if kind == RequirementCommentAnchorKind.REQUIREMENT_DETAIL:
            self._validate_requirement_detail_anchor(db, target, target_anchor)
            return
        if kind == RequirementCommentAnchorKind.REQUIREMENT_LINK:
            self._validate_requirement_link_anchor(db, target, target_anchor)
            return
        if kind == RequirementCommentAnchorKind.REQUIREMENT_RELATION:
            self._validate_requirement_relation_anchor(db, target, target_anchor)
            return
        if kind == RequirementCommentAnchorKind.REQUIREMENT_TASK:
            self._validate_requirement_task_anchor(
                db,
                project_id=project_id,
                target=target,
                target_anchor=target_anchor,
            )
            return

        raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)

    def _validate_requirement_detail_anchor(
        self,
        db: Session,
        target: CommentTarget,
        target_anchor: dict,
    ) -> RequirementDetail:
        """実現内容アンカーが対象要件に属することを確認する。"""
        self._ensure_anchor_target_is_requirement(target)
        detail_id = self._get_int_anchor_value(target_anchor, "detail_id")
        detail = self.detail_repository.get_by_id(db, detail_id)
        if detail is None or detail.requirement_id != target.target_id:
            raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)
        return detail

    def _validate_requirement_link_anchor(
        self,
        db: Session,
        target: CommentTarget,
        target_anchor: dict,
    ) -> RequirementLink:
        """関連成果物アンカーが対象要件に属することを確認する。"""
        self._ensure_anchor_target_is_requirement(target)
        link_id = self._get_int_anchor_value(target_anchor, "link_id")
        link = self.link_repository.get_by_id(db, link_id)
        if link is None or link.requirement_id != target.target_id:
            raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)
        return link

    def _validate_requirement_relation_anchor(
        self,
        db: Session,
        target: CommentTarget,
        target_anchor: dict,
    ) -> RequirementRelation:
        """要件関連アンカーが対象要件に属することを確認する。"""
        self._ensure_anchor_target_is_requirement(target)
        relation_id = self._get_int_anchor_value(target_anchor, "relation_id")
        relation = self.relation_repository.get_by_id(db, relation_id)
        if (
            relation is None
            or relation.document_id != target.document_id
            or relation.source_requirement_id != target.target_id
        ):
            raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)
        return relation

    def _validate_requirement_task_anchor(
        self,
        db: Session,
        *,
        project_id: int,
        target: CommentTarget,
        target_anchor: dict,
    ) -> RequirementTaskRelation:
        """関連タスクアンカーが対象要件に紐づくことを確認する。"""
        self._ensure_anchor_target_is_requirement(target)
        relation_id = self._get_optional_int_anchor_value(
            target_anchor,
            "relation_id",
        )
        task_id = self._get_optional_int_anchor_value(target_anchor, "task_id")
        relation = (
            self.task_relation_repository.get_by_id(db, relation_id)
            if relation_id is not None
            else self.task_relation_repository.get_by_requirement_and_task(
                db,
                requirement_id=target.target_id,
                task_id=task_id,
            )
        )
        if (
            relation is None
            or relation.requirement_id != target.target_id
            or (task_id is not None and relation.task_id != task_id)
        ):
            raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)

        task = self.task_repository.get_by_id(db, relation.task_id)
        if task is None or task.project_id != project_id:
            raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)
        return relation

    def _ensure_anchor_target_is_requirement(self, target: CommentTarget) -> None:
        """要件詳細内アンカーは要件コメントにだけ紐づける。"""
        if target.target_type != RequirementCommentTargetType.REQUIREMENT_ITEM:
            raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)

    def _get_int_anchor_value(self, target_anchor: dict, key: str) -> int:
        """アンカー内の必須整数値を取得する。"""
        value = self._get_optional_int_anchor_value(target_anchor, key)
        if value is None:
            raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)
        return value

    def _get_optional_int_anchor_value(
        self,
        target_anchor: dict,
        key: str,
    ) -> int | None:
        """アンカー内の任意整数値を取得する。"""
        value = target_anchor.get(key)
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)
