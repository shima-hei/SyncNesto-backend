"""既存の検索/正式関連を上限付きSQLで読む。HTTPと認可はServiceが扱う。"""

from sqlalchemy import and_, func, literal, select
from sqlalchemy.orm import Session

from app.models.document import DocumentLink, ProjectDocument
from app.models.requirement import Requirement, RequirementDocument, RequirementSection
from app.models.specification import Specification
from app.models.task import Milestone, Task
from app.models.test_design import TestItem
from app.repositories.requirement_trace import RequirementTraceRepository
from app.repositories.search import SearchRepository


class McpReadRepository:
    """本文や全関連をPythonへ取り込んでからページ分割しない。"""

    def __init__(self):
        """正式関連と既存の検索条件を共有する。"""
        self.trace = RequirementTraceRepository()
        self.search = SearchRepository()

    def search_items(self, db, project, user_id, data, *, include_objectives=False):
        """既存のtenant/所属/種類別read条件をUNION前に適用する。"""
        rows = self.search.sources(
            project.tenant_id,
            user_id,
            data.query,
            project.id,
            include_objectives=include_objectives,
            include_metadata=True,
        )
        query = select(rows)
        if data.entity_types is not None:
            query = query.where(rows.c.kind.in_(data.entity_types))
        if data.status is not None:
            query = query.where(rows.c.status == data.status)
        return self.page(
            db, query, (rows.c.rank, rows.c.kind, rows.c.id), data.page, data.page_size
        )

    def page(self, db, query, order, page, page_size):
        """同じ絞込条件で件数を求め、結果は指定件数だけ取得する。"""
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = (
            db.execute(
                query.order_by(*order).offset((page - 1) * page_size).limit(page_size)
            )
            .mappings()
            .all()
        )
        return total, rows

    def requirement(self, db: Session, project_id, requirement_id, text_limit):
        """要件/文書/章を一つのSQLで読み、本文はDB側で切り詰める。"""
        section = RequirementSection
        fields = [
            Requirement.id,
            Requirement.requirement_code.label("code"),
            Requirement.title,
            Requirement.status,
            Requirement.version,
            Requirement.updated_at,
            Requirement.document_id,
            RequirementDocument.document_code,
            RequirementDocument.title.label("document_title"),
            RequirementDocument.version.label("document_version"),
            RequirementDocument.updated_at.label("document_updated_at"),
            section.id.label("section_id"),
            section.title.label("section_title"),
            section.version.label("section_version"),
            section.updated_at.label("section_updated_at"),
        ]
        for name in ("description", "acceptance_criteria", "rationale"):
            value = (
                func.left(getattr(Requirement, name), text_limit + 1)
                if text_limit
                else literal(None)
            )
            fields.append(value.label(name))
        return (
            db.execute(
                select(*fields)
                .join(
                    RequirementDocument,
                    RequirementDocument.id == Requirement.document_id,
                )
                .outerjoin(
                    section,
                    and_(
                        section.id == Requirement.section_id,
                        section.document_id == Requirement.document_id,
                    ),
                )
                .where(*self.trace.scope(project_id), Requirement.id == requirement_id)
            )
            .mappings()
            .one_or_none()
        )

    def related(self, db, project_id, requirement_id, kind, page, page_size):
        """両端の案件と削除状態を検査済みの正式関連だけを利用する。"""
        if kind == "documents":
            model = ProjectDocument
            query = (
                select(
                    model.id.label("target_id"),
                    model.title,
                    model.version,
                    model.updated_at,
                    literal(None).label("status"),
                    literal(None).label("code"),
                )
                .join(DocumentLink, DocumentLink.document_id == model.id)
                .where(
                    DocumentLink.requirement_id == requirement_id,
                    model.project_id == project_id,
                    model.deleted_at.is_(None),
                )
            )
            return self.page(db, query, (model.id,), page, page_size)
        trace_kind = "test_items" if kind == "tests" else kind
        model = {"designs": Specification, "tasks": Task, "tests": TestItem}[kind]
        rows = self.trace.related_query(
            project_id, [requirement_id], trace_kind
        ).subquery()
        query = (
            select(
                rows, getattr(model, "updated_at", literal(None)).label("updated_at")
            )
            .join(model, model.id == rows.c.target_id)
            .distinct()
        )
        return self.page(db, query, (rows.c.code, rows.c.target_id), page, page_size)

    def task_counts(self, db, project_id, as_of):
        """既存TaskServiceの期限超過条件を、同案件の有効行へ適用する。"""
        scope = (Task.project_id == project_id, Task.deleted_at.is_(None))
        total, overdue = db.execute(
            select(
                func.count(Task.id),
                func.count(Task.id).filter(
                    Task.due_date < as_of, Task.status.not_in(("done", "cancelled"))
                ),
            ).where(*scope)
        ).one()
        statuses = db.execute(
            select(Task.status, func.count())
            .where(*scope)
            .group_by(Task.status)
            .order_by(Task.status)
            .limit(101)
        ).all()
        return total, overdue, statuses

    def objective(self, db, project_id, document_id):
        """概要向けに必要な出典と2001字だけをDBから読む。"""
        return (
            db.execute(
                select(
                    RequirementDocument.id,
                    RequirementDocument.document_code,
                    RequirementDocument.title,
                    RequirementDocument.version,
                    RequirementDocument.updated_at,
                    *(
                        func.left(
                            getattr(RequirementDocument, f"project_{field}"), 2001
                        ).label(field)
                        for field in ("goal", "scope", "completion_criteria")
                    ),
                ).where(
                    RequirementDocument.id == document_id,
                    RequirementDocument.project_id == project_id,
                    RequirementDocument.deleted_at.is_(None),
                )
            )
            .mappings()
            .one_or_none()
        )

    def milestones(self, db, project_id, limit):
        """説明本文は展開せず、日付順の節目を返す。"""
        query = select(
            Milestone.id.label("target_id"),
            Milestone.title,
            Milestone.status,
            Milestone.version,
            Milestone.updated_at,
            Milestone.target_date,
        ).where(Milestone.project_id == project_id, Milestone.deleted_at.is_(None))
        return self.page(db, query, (Milestone.target_date, Milestone.id), 1, limit)
