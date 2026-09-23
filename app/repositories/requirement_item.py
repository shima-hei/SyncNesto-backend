"""要件定義Repositoryを定義するモジュール。"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.models.requirement import (
    Requirement,
)
from app.schemas.requirement import (
    RequirementCreate,
    RequirementUpdate,
)


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
            section_id=requirement_in.section_id,
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

    def list_by_ids(self, db: Session, requirement_ids: list[int]) -> list[Requirement]:
        """id一覧に一致する要件一覧を取得する。"""
        if not requirement_ids:
            return []

        return (
            db.query(Requirement)
            .filter(
                Requirement.id.in_(requirement_ids),
                Requirement.deleted_at.is_(None),
            )
            .order_by(Requirement.id)
            .all()
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

    def get_max_auto_requirement_number(self, db: Session, document_id: int) -> int:
        """要件定義書内の自動採番要件コード最大番号を取得する。

        Args:
            db: DBセッション。
            document_id: 採番対象の要件定義書ID。

        Returns:
            `REQ-001` 形式の最大番号。存在しない場合は0。
        """
        result = db.execute(
            text(
                """
                SELECT COALESCE(
                    MAX(CAST(
                        substring(requirement_code from '^REQ-(\\d+)$') AS INTEGER
                    )),
                    0
                )
                FROM requirements
                WHERE document_id = :document_id
                  AND requirement_code ~ '^REQ-[0-9]+$'
                """
            ),
            {"document_id": document_id},
        ).scalar_one()
        return int(result)

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
        section_id: int | None = None,
        priority: str | None = None,
        owner_id: int | None = None,
        sort: str | None = None,
        sort_by: str | None = None,
        sort_order: str | None = None,
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
        if section_id is not None:
            query = query.filter(Requirement.section_id == section_id)
        if priority is not None:
            query = query.filter(Requirement.priority == priority)
        if owner_id is not None:
            query = query.filter(Requirement.owner_id == owner_id)

        total = query.count()
        order_by = self._build_order_by(
            sort=sort,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        requirements = (
            query.order_by(*order_by)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return requirements, total

    def _build_order_by(
        self,
        *,
        sort: str | None,
        sort_by: str | None,
        sort_order: str | None,
    ) -> list[Any]:
        """要件一覧のソート条件を作成する。"""
        sort_map: dict[str, list[Any]] = {
            "updated_desc": [Requirement.updated_at.desc(), Requirement.id.desc()],
            "updated_asc": [Requirement.updated_at.asc(), Requirement.id.asc()],
            "updated_at_desc": [Requirement.updated_at.desc(), Requirement.id.desc()],
            "updated_at_asc": [Requirement.updated_at.asc(), Requirement.id.asc()],
            "code_asc": [Requirement.requirement_code.asc(), Requirement.id.asc()],
            "code_desc": [Requirement.requirement_code.desc(), Requirement.id.desc()],
            "requirement_code_asc": [
                Requirement.requirement_code.asc(),
                Requirement.id.asc(),
            ],
            "requirement_code_desc": [
                Requirement.requirement_code.desc(),
                Requirement.id.desc(),
            ],
            "title_asc": [Requirement.title.asc(), Requirement.id.asc()],
            "title_desc": [Requirement.title.desc(), Requirement.id.desc()],
            "priority_asc": [Requirement.priority.asc(), Requirement.id.asc()],
            "priority_desc": [Requirement.priority.desc(), Requirement.id.desc()],
            "status_asc": [Requirement.status.asc(), Requirement.id.asc()],
            "status_desc": [Requirement.status.desc(), Requirement.id.desc()],
        }
        if sort:
            return sort_map.get(sort, sort_map["updated_desc"])
        if sort_by:
            normalized_order = "asc" if sort_order == "asc" else "desc"
            return sort_map.get(
                f"{sort_by}_{normalized_order}",
                sort_map["updated_desc"],
            )
        return sort_map["updated_desc"]

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
            "section_id",
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

    def list_by_document(
        self,
        db: Session,
        document_id: int,
    ) -> list[Requirement]:
        """指定要件定義書の要件一覧を取得する。"""
        return self.list_by_document_ids(db, [document_id])
