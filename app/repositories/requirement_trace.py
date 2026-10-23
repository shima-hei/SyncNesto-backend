"""表示pageの要件ID集合だけを対象に専門関連を集約する。"""

from sqlalchemy import exists, func, or_, select

from app.models.requirement import Requirement, RequirementDocument, RequirementSection
from app.models.specification import Specification, SpecificationLink
from app.models.task import RequirementTaskRelation, Task
from app.models.test_design import RequirementTestItem, TestDesign, TestItem

EXCERPT_LIMIT = 8000
RELATED_LIMIT = 20
ROW_COLUMNS = (
    Requirement.id,
    Requirement.document_id,
    RequirementDocument.document_code,
    RequirementDocument.title.label("document_title"),
    Requirement.requirement_code,
    Requirement.title,
    Requirement.version,
    Requirement.status,
)


class RequirementTraceRepository:
    """HTTP/認可に依存せず、同案件の有効な正本だけを読む。"""

    def scope(self, project_id):
        """削除された文書/章や、壊れた所属を除外する。"""
        return (
            RequirementDocument.project_id == project_id,
            RequirementDocument.deleted_at.is_(None),
            Requirement.deleted_at.is_(None),
            or_(
                Requirement.section_id.is_(None),
                exists().where(
                    RequirementSection.id == Requirement.section_id,
                    RequirementSection.document_id == Requirement.document_id,
                    RequirementSection.deleted_at.is_(None),
                ),
            ),
        )

    def rows(self, db, project_id, page, page_size, document_id, q, status):
        """countとpageの2 SQL。本文は検索に使っても応答へ取得しない。"""
        query = (
            select(*ROW_COLUMNS)
            .join(
                RequirementDocument, RequirementDocument.id == Requirement.document_id
            )
            .where(*self.scope(project_id))
        )
        if document_id is not None:
            query = query.where(Requirement.document_id == document_id)
        if q:
            pattern = f"%{q}%"
            query = query.where(
                or_(
                    Requirement.requirement_code.ilike(pattern),
                    Requirement.title.ilike(pattern),
                    Requirement.description.ilike(pattern),
                )
            )
        if status:
            query = query.where(Requirement.status == status)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = (
            db.execute(
                query.order_by(Requirement.requirement_code, Requirement.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .mappings()
            .all()
        )
        return rows, total

    def detail(self, db, project_id, requirement_id):
        """長い本文を丸ごとPythonへ読み込まない。"""
        return (
            db.execute(
                select(
                    *ROW_COLUMNS,
                    func.left(Requirement.description, EXCERPT_LIMIT + 1).label(
                        "description"
                    ),
                    func.left(Requirement.acceptance_criteria, EXCERPT_LIMIT + 1).label(
                        "acceptance_criteria"
                    ),
                )
                .join(
                    RequirementDocument,
                    RequirementDocument.id == Requirement.document_id,
                )
                .where(*self.scope(project_id), Requirement.id == requirement_id)
            )
            .mappings()
            .one_or_none()
        )

    def related_query(self, project_id, requirement_ids, kind):
        """関係の両端と専門画面の親が同Project内で有効なものだけ。"""
        if kind == "designs":
            return (
                select(
                    SpecificationLink.requirement_id,
                    Specification.id.label("target_id"),
                    func.concat(Specification.kind, "-", Specification.id).label(
                        "code"
                    ),
                    Specification.title,
                    Specification.status,
                    Specification.version,
                )
                .join(
                    Specification,
                    Specification.id == SpecificationLink.specification_id,
                )
                .where(
                    SpecificationLink.requirement_id.in_(requirement_ids),
                    Specification.project_id == project_id,
                    Specification.deleted_at.is_(None),
                )
            )
        if kind == "tasks":
            return (
                select(
                    RequirementTaskRelation.requirement_id,
                    Task.id.label("target_id"),
                    Task.task_code.label("code"),
                    Task.title,
                    Task.status,
                    Task.version,
                )
                .join(Task, Task.id == RequirementTaskRelation.task_id)
                .where(
                    RequirementTaskRelation.requirement_id.in_(requirement_ids),
                    Task.project_id == project_id,
                    Task.deleted_at.is_(None),
                )
            )
        return (
            select(
                RequirementTestItem.requirement_id,
                TestItem.id.label("target_id"),
                TestItem.code,
                func.left(TestItem.content, 200).label("title"),
                TestDesign.id.label("design_id"),
                TestDesign.name.label("context_title"),
                TestDesign.version,
                TestItem.position,
            )
            .join(TestItem, TestItem.id == RequirementTestItem.item_id)
            .join(TestDesign, TestDesign.id == TestItem.design_id)
            .where(
                RequirementTestItem.requirement_id.in_(requirement_ids),
                TestItem.deleted_at.is_(None),
                TestItem.is_spacer.is_(False),
                TestDesign.project_id == project_id,
                TestDesign.deleted_at.is_(None),
            )
        )

    def counts(self, db, project_id, requirement_ids, kind):
        """各REQの関連先IDをdistinctし、1 SQLで数える。"""
        if not requirement_ids:
            return {}
        related = self.related_query(project_id, requirement_ids, kind).subquery()
        return dict(
            db.execute(
                select(
                    related.c.requirement_id,
                    func.count(func.distinct(related.c.target_id)),
                ).group_by(related.c.requirement_id)
            ).all()
        )

    def targets(self, db, project_id, requirement_id, kind):
        """選択1件につき各20関連だけを返し、全件走査を避ける。"""
        query = self.related_query(project_id, [requirement_id], kind)
        return (
            db.execute(query.order_by("target_id").limit(RELATED_LIMIT))
            .mappings()
            .all()
        )
