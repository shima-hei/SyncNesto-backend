"""要件定義Repositoryを定義するモジュール。"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.requirement import (
    RequirementSection,
)
from app.schemas.requirement import (
    RequirementSectionCreate,
    RequirementSectionUpdate,
)


class RequirementSectionRepository:
    """RequirementSectionテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        document_id: int,
        section_in: RequirementSectionCreate,
        actor_id: int | None = None,
    ) -> RequirementSection:
        """要件定義セクションを作成する。"""
        section = RequirementSection(
            document_id=document_id,
            title=section_in.title,
            section_type=section_in.section_type,
            content=section_in.content,
            sort_order=section_in.sort_order,
            status=section_in.status,
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(section)
        db.commit()
        db.refresh(section)
        return section

    def get_by_id(self, db: Session, section_id: int) -> RequirementSection | None:
        """idに一致する要件定義セクションを取得する。"""
        return (
            db.query(RequirementSection)
            .filter(
                RequirementSection.id == section_id,
                RequirementSection.deleted_at.is_(None),
            )
            .first()
        )

    def list_by_document(
        self,
        db: Session,
        document_id: int,
    ) -> list[RequirementSection]:
        """指定要件定義書のセクション一覧を取得する。"""
        return (
            db.query(RequirementSection)
            .filter(
                RequirementSection.document_id == document_id,
                RequirementSection.deleted_at.is_(None),
            )
            .order_by(RequirementSection.sort_order, RequirementSection.id)
            .all()
        )

    def update(
        self,
        db: Session,
        *,
        section: RequirementSection,
        section_in: RequirementSectionUpdate,
        actor_id: int | None = None,
    ) -> RequirementSection:
        """要件定義セクションを更新する。"""
        for field in ("title", "section_type", "content", "sort_order", "status"):
            if field in section_in.model_fields_set:
                setattr(section, field, getattr(section_in, field))
        if actor_id is not None:
            section.updated_by = actor_id
        section.version += 1

        db.commit()
        db.refresh(section)
        return section

    def update_sort_orders(
        self,
        db: Session,
        *,
        sections: list[RequirementSection],
        sort_orders_by_id: dict[int, int],
        actor_id: int | None = None,
    ) -> list[RequirementSection]:
        """要件定義セクションの表示順をまとめて更新する。"""
        for section in sections:
            section.sort_order = sort_orders_by_id[section.id]
            if actor_id is not None:
                section.updated_by = actor_id
            section.version += 1

        db.commit()
        for section in sections:
            db.refresh(section)
        return sorted(sections, key=lambda item: (item.sort_order, item.id))

    def soft_delete(
        self,
        db: Session,
        *,
        section: RequirementSection,
        actor_id: int | None = None,
    ) -> RequirementSection:
        """要件定義セクションを論理削除する。"""
        section.deleted_at = datetime.now(UTC)
        if actor_id is not None:
            section.updated_by = actor_id
        db.commit()
        db.refresh(section)
        return section
