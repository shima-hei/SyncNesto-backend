"""要件関連Serviceを定義するモジュール。"""

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import NotFoundError
from app.models.requirement import (
    RequirementRelation,
)
from app.schemas.requirement import (
    RequirementRelationCreate,
)
from app.services.requirement_change_log import (
    RequirementChangeLogAction,
    RequirementChangeLogTargetType,
)
from app.services.requirement_child_base import RequirementChildBaseService


class RequirementRelationService(RequirementChildBaseService):
    """要件関連Serviceを定義するモジュール。"""

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
