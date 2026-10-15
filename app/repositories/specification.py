"""設計書と関連候補のProject単位の永続化。"""

from uuid import UUID

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session, load_only

from app.models.document import ProjectDocument
from app.models.requirement import Requirement, RequirementDocument
from app.models.specification import (
    Specification,
    SpecificationComment,
    SpecificationLink,
    SpecificationRevision,
)
from app.models.task import Task
from app.models.test_design import TestDesign, TestItem
from app.schemas.specification import SpecificationRead

TARGETS = {
    "requirement": (
        Requirement,
        "requirement_id",
        Requirement.title,
        RequirementDocument,
        Requirement.document_id,
    ),
    "task": (Task, "task_id", Task.title, None, None),
    "test_item": (
        TestItem,
        "test_item_id",
        TestItem.content,
        TestDesign,
        TestItem.design_id,
    ),
    "specification": (
        Specification,
        "related_specification_id",
        Specification.title,
        None,
        None,
    ),
    "document": (ProjectDocument, "document_id", ProjectDocument.title, None, None),
}

SUMMARY_COLUMNS = (
    Specification.id,
    Specification.kind,
    Specification.title,
    Specification.summary,
    Specification.status,
    Specification.version,
    Specification.updated_at,
)


class SpecificationRepository:
    """本文の更新と関連の変更は親行をロックして直列化する。"""

    def get(
        self, db: Session, project_id: int, specification_id: int, *, lock: bool = False
    ):
        query = select(Specification).where(
            Specification.id == specification_id,
            Specification.project_id == project_id,
            Specification.deleted_at.is_(None),
        )
        return db.scalar(query.with_for_update() if lock else query)

    def list(
        self,
        db: Session,
        project_id: int,
        kind: str | None,
        q: str,
        page: int,
        page_size: int,
    ):
        query = select(Specification).where(
            Specification.project_id == project_id, Specification.deleted_at.is_(None)
        )
        if kind:
            query = query.where(Specification.kind == kind)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(
                or_(
                    Specification.title.ilike(f"%{escaped}%", escape="\\"),
                    Specification.summary.ilike(f"%{escaped}%", escape="\\"),
                )
            )
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.scalars(
            query.options(load_only(*SUMMARY_COLUMNS))
            .order_by(Specification.updated_at.desc(), Specification.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return rows, total

    def revision(self, db: Session, row: Specification, actor_id: int):
        db.flush()
        db.add(
            SpecificationRevision(
                specification_id=row.id,
                number=row.version,
                snapshot=SpecificationRead.model_validate(row).model_dump(mode="json"),
                created_by=actor_id,
            )
        )

    def revisions(self, db: Session, specification_id: int):
        return db.scalars(
            select(SpecificationRevision)
            .options(
                load_only(
                    SpecificationRevision.number,
                    SpecificationRevision.created_by,
                    SpecificationRevision.created_at,
                )
            )
            .where(SpecificationRevision.specification_id == specification_id)
            .order_by(SpecificationRevision.number.desc())
            .limit(100)
        ).all()

    def comments(
        self,
        db: Session,
        specification_id: int,
        page: int = 1,
        page_size: int | None = None,
    ):
        query = (
            select(SpecificationComment)
            .where(SpecificationComment.specification_id == specification_id)
            .order_by(SpecificationComment.created_at, SpecificationComment.id)
        )
        if page_size is not None:
            query = query.offset((page - 1) * page_size).limit(page_size)
        return db.scalars(query).all()

    def comment_count(self, db: Session, specification_id: int) -> int:
        """投稿数の検証で指摘本文を転送しない。"""
        return (
            db.scalar(
                select(func.count())
                .select_from(SpecificationComment)
                .where(SpecificationComment.specification_id == specification_id)
            )
            or 0
        )

    def has_unresolved_comments(self, db: Session, specification_id: int) -> bool:
        """承認可否に必要な存在だけを調べる。"""
        return (
            db.scalar(
                select(SpecificationComment.id)
                .where(
                    SpecificationComment.specification_id == specification_id,
                    SpecificationComment.is_resolved.is_(False),
                )
                .limit(1)
            )
            is not None
        )

    def comment(self, db: Session, specification_id: int, comment_id: int):
        """親設計書に所属する指摘1件だけを取得する。"""
        return db.scalar(
            select(SpecificationComment).where(
                SpecificationComment.specification_id == specification_id,
                SpecificationComment.id == comment_id,
            )
        )

    def revision_number(self, db: Session, specification_id: int, number: int):
        return db.scalar(
            select(SpecificationRevision).where(
                SpecificationRevision.specification_id == specification_id,
                SpecificationRevision.number == number,
            )
        )

    def target_query(self, project_id: int, kind: str):
        model, _, title, parent, parent_key = TARGETS[kind]
        query = select(
            model.id.label("id"),
            title.label("title"),
            (parent_key if parent is not None else model.id).label("container_id"),
        ).where(model.deleted_at.is_(None))
        if parent is not None:
            query = query.join(parent, parent_key == parent.id).where(
                parent.project_id == project_id, parent.deleted_at.is_(None)
            )
        else:
            query = query.where(model.project_id == project_id)
        if kind == "test_item":
            query = query.where(TestItem.is_spacer.is_(False))
        return query

    def target(self, db: Session, project_id: int, kind: str, target_id: str):
        model = TARGETS[kind][0]
        try:
            identifier = UUID(target_id) if kind == "test_item" else int(target_id)
            if isinstance(identifier, int) and not 0 < identifier <= 2147483647:
                return None
        except ValueError:
            return None
        return (
            db.execute(
                self.target_query(project_id, kind).where(model.id == identifier)
            )
            .mappings()
            .first()
        )

    def candidates(self, db: Session, project_id: int, kind: str, q: str):
        model, _, title, _, _ = TARGETS[kind]
        query = self.target_query(project_id, kind)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(
                or_(
                    title.ilike(f"%{escaped}%", escape="\\"),
                    cast(model.id, String) == q,
                )
            )
        return db.execute(query.order_by(model.id).limit(50)).mappings().all()

    def links(self, db: Session, specification_id: int):
        return db.scalars(
            select(SpecificationLink).where(
                SpecificationLink.specification_id == specification_id
            )
        ).all()

    def related(self, db: Session, project_id: int, kind: str, target_id: str):
        column = getattr(SpecificationLink, TARGETS[kind][1])
        identifier = UUID(target_id) if kind == "test_item" else int(target_id)
        return db.scalars(
            select(Specification)
            .options(load_only(*SUMMARY_COLUMNS))
            .join(
                SpecificationLink,
                SpecificationLink.specification_id == Specification.id,
            )
            .where(
                Specification.project_id == project_id,
                Specification.deleted_at.is_(None),
                column == identifier,
            )
            .distinct()
            .order_by(Specification.id)
            .limit(100)
        ).all()
