"""タスク管理Repositoryを定義するモジュール。"""

from typing import Any

from sqlalchemy.orm import Session

from app.models.requirement import Requirement, RequirementDocument
from app.models.task import (
    RequirementTaskRelation,
    Task,
)


class RequirementTaskRelationRepository:
    """RequirementTaskRelationテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        requirement_id: int,
        task_id: int,
        relation_type: str,
        actor_id: int | None,
    ) -> RequirementTaskRelation:
        """要件タスク関連を作成する。"""
        relation = RequirementTaskRelation(
            requirement_id=requirement_id,
            task_id=task_id,
            relation_type=relation_type,
            created_by=actor_id,
        )
        db.add(relation)
        db.commit()
        db.refresh(relation)
        return relation

    def get_by_id(
        self,
        db: Session,
        relation_id: int,
    ) -> RequirementTaskRelation | None:
        """idに一致する要件タスク関連を取得する。"""
        return (
            db.query(RequirementTaskRelation)
            .filter(RequirementTaskRelation.id == relation_id)
            .first()
        )

    def list_by_requirement(
        self,
        db: Session,
        requirement_id: int,
    ) -> list[RequirementTaskRelation]:
        """要件に紐づくタスク関連一覧を取得する。"""
        return (
            db.query(RequirementTaskRelation)
            .filter(RequirementTaskRelation.requirement_id == requirement_id)
            .order_by(RequirementTaskRelation.id)
            .all()
        )

    def list_tasks_by_requirement(
        self,
        db: Session,
        requirement_id: int,
    ) -> list[Task]:
        """要件に紐づく未削除タスク一覧を取得する。"""
        return (
            db.query(Task)
            .join(RequirementTaskRelation, RequirementTaskRelation.task_id == Task.id)
            .filter(
                RequirementTaskRelation.requirement_id == requirement_id,
                Task.deleted_at.is_(None),
            )
            .order_by(Task.sort_order, Task.id)
            .all()
        )

    def list_requirement_summaries_by_task_ids(
        self,
        db: Session,
        task_ids: list[int],
    ) -> dict[int, list[dict[str, Any]]]:
        """タスクIDごとの関連要件概要を取得する。

        Args:
            db: DBセッション。
            task_ids: タスクID一覧。

        Returns:
            task_idをキー、関連要件概要一覧を値にした辞書。
        """
        if not task_ids:
            return {}
        rows = (
            db.query(
                RequirementTaskRelation.task_id,
                RequirementTaskRelation.id.label("relation_id"),
                RequirementTaskRelation.relation_type,
                Requirement.id.label("requirement_id"),
                Requirement.requirement_code,
                Requirement.title,
            )
            .join(Requirement, RequirementTaskRelation.requirement_id == Requirement.id)
            .filter(
                RequirementTaskRelation.task_id.in_(task_ids),
                Requirement.deleted_at.is_(None),
            )
            .order_by(RequirementTaskRelation.id)
            .all()
        )
        summaries: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            summaries.setdefault(row.task_id, []).append(
                {
                    "id": row.requirement_id,
                    "requirement_code": row.requirement_code,
                    "title": row.title,
                    "relation_id": row.relation_id,
                    "relation_type": row.relation_type,
                }
            )
        return summaries

    def delete(self, db: Session, relation: RequirementTaskRelation) -> None:
        """要件タスク関連を削除する。"""
        db.delete(relation)
        db.commit()

class TaskRequirementLookupRepository:
    """タスク機能から要件と要件定義書を参照するRepository。"""

    def get_requirement(self, db: Session, requirement_id: int) -> Requirement | None:
        """idに一致する未削除要件を取得する。"""
        return (
            db.query(Requirement)
            .filter(Requirement.id == requirement_id, Requirement.deleted_at.is_(None))
            .first()
        )

    def list_requirements_by_ids(
        self,
        db: Session,
        requirement_ids: list[int],
    ) -> list[Requirement]:
        """id一覧に一致する未削除要件を取得する。

        Args:
            db: DBセッション。
            requirement_ids: 取得対象の要件ID一覧。

        Returns:
            未削除要件一覧。
        """
        if not requirement_ids:
            return []
        return (
            db.query(Requirement)
            .filter(
                Requirement.id.in_(requirement_ids),
                Requirement.deleted_at.is_(None),
            )
            .all()
        )

    def get_requirement_project_id(
        self,
        db: Session,
        requirement_id: int,
    ) -> int | None:
        """要件が属するプロジェクトIDを取得する。"""
        row = (
            db.query(RequirementDocument.project_id)
            .join(Requirement, Requirement.document_id == RequirementDocument.id)
            .filter(
                Requirement.id == requirement_id,
                Requirement.deleted_at.is_(None),
                RequirementDocument.deleted_at.is_(None),
            )
            .first()
        )
        return row[0] if row is not None else None
