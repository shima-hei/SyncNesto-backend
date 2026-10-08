"""組織・所属・種類ごとの権限をSQLで適用する横断検索。"""

from sqlalchemy import (
    Integer,
    Select,
    String,
    case,
    cast,
    func,
    literal,
    or_,
    select,
    union_all,
)
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import Subquery

from app.models.document import ProjectDocument
from app.models.project import Project, ProjectMember
from app.models.rbac import Permission, Role, RolePermission
from app.models.requirement import Requirement, RequirementDocument
from app.models.task import Task
from app.models.test_design import TestCase, TestDesign, TestItem

READ_PERMISSIONS = (
    "requirement:read",
    "task:read",
    "test_plan:read",
    "test_case:read",
    "document:read",
)


def escaped_pattern(q: str) -> str:
    """LIKEの特殊文字も検索語そのものとして扱う。"""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class SearchRepository:
    """検索と集計をDB内で完結させ、案件数によるN+1を作らない。"""

    def project_ids(
        self, tenant_id: int, user_id: int, permissions: tuple[str, ...]
    ) -> Select:
        """Project閲覧と対象の閲覧権限を持つ有効な所属を選ぶ。"""
        query = (
            select(Project.id)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(
                Project.tenant_id == tenant_id,
                Project.deleted_at.is_(None),
                ProjectMember.user_id == user_id,
                ProjectMember.deleted_at.is_(None),
            )
        )
        for codes in (("project:read",), permissions):
            roles = (
                select(RolePermission.role_id)
                .join(Role)
                .join(Permission)
                .where(Role.scope == "project", Permission.code.in_(codes))
            )
            query = query.where(ProjectMember.role_id.in_(roles))
        return query

    def sources(
        self, tenant_id: int, user_id: int, q: str, project_id: int | None
    ) -> Subquery:
        """各リソースを同じ列構成へ揃え、UNION前に境界を確定する。"""
        pattern = escaped_pattern(q)

        def source(
            kind,
            category,
            model,
            title,
            code,
            fields,
            permission,
            *,
            parent=None,
            container=None,
            conditions=(),
        ):
            body = func.concat_ws("\n", *fields)
            title = func.coalesce(title, "")
            code = func.coalesce(code, "")
            rank = case(
                (func.lower(title) == q.lower(), 0),
                (title.ilike(pattern, escape="\\"), 1),
                (code.ilike(pattern, escape="\\"), 2),
                else_=3,
            )
            start = func.greatest(func.strpos(func.lower(body), q.lower()) - 60, 1)
            query = select(
                literal(kind).label("kind"),
                literal(category).label("category"),
                cast(model.id, String).label("id"),
                Project.id.label("project_id"),
                Project.name.label("project_name"),
                Project.project_code,
                cast(
                    container if container is not None else literal(None),
                    Integer,
                ).label("container_id"),
                func.substr(title, 1, 240).label("title"),
                code.label("code"),
                func.substr(body, start, 240).label("excerpt"),
                rank.label("rank"),
            ).select_from(model)
            if parent is not None:
                query = query.join(
                    parent,
                    model.document_id == parent.id
                    if parent is RequirementDocument
                    else model.design_id == parent.id,
                )
            owner = parent if parent is not None else model
            query = query.join(Project, owner.project_id == Project.id).where(
                Project.id.in_(self.project_ids(tenant_id, user_id, (permission,))),
                or_(
                    title.ilike(pattern, escape="\\"),
                    code.ilike(pattern, escape="\\"),
                    body.ilike(pattern, escape="\\"),
                ),
                *conditions,
            )
            if project_id is not None:
                query = query.where(Project.id == project_id)
            return query

        return union_all(
            source(
                "requirement_document",
                "requirement",
                RequirementDocument,
                RequirementDocument.title,
                RequirementDocument.document_code,
                (RequirementDocument.purpose, RequirementDocument.target_system_name),
                "requirement:read",
                conditions=(RequirementDocument.deleted_at.is_(None),),
            ),
            source(
                "requirement",
                "requirement",
                Requirement,
                Requirement.title,
                Requirement.requirement_code,
                (
                    Requirement.description,
                    Requirement.rationale,
                    Requirement.acceptance_criteria,
                ),
                "requirement:read",
                parent=RequirementDocument,
                container=Requirement.document_id,
                conditions=(
                    Requirement.deleted_at.is_(None),
                    RequirementDocument.deleted_at.is_(None),
                ),
            ),
            source(
                "task",
                "task",
                Task,
                Task.title,
                Task.task_code,
                (Task.description,),
                "task:read",
                conditions=(Task.deleted_at.is_(None),),
            ),
            source(
                "test_design",
                "test",
                TestDesign,
                TestDesign.name,
                literal(""),
                (TestDesign.description,),
                "test_plan:read",
                conditions=(TestDesign.deleted_at.is_(None),),
            ),
            source(
                "test_item",
                "test",
                TestItem,
                func.coalesce(func.nullif(TestItem.content, ""), TestItem.code),
                TestItem.code,
                (
                    TestItem.target_feature,
                    TestItem.viewpoint,
                    TestItem.content,
                    TestItem.preconditions,
                    TestItem.test_data,
                    TestItem.steps,
                    TestItem.expected_result,
                    TestItem.notes,
                ),
                "test_plan:read",
                parent=TestDesign,
                container=TestItem.design_id,
                conditions=(
                    TestItem.deleted_at.is_(None),
                    TestItem.is_spacer.is_(False),
                    TestDesign.deleted_at.is_(None),
                ),
            ),
            source(
                "test_case",
                "test",
                TestCase,
                func.coalesce(
                    func.nullif(TestCase.source["item"]["content"].astext, ""),
                    TestCase.source["item"]["code"].astext,
                    literal("テストケース"),
                ),
                TestCase.source["item"]["code"].astext,
                tuple(
                    TestCase.source["item"][key].astext
                    for key in (
                        "target_feature",
                        "viewpoint",
                        "content",
                        "preconditions",
                        "test_data",
                        "steps",
                        "expected_result",
                        "notes",
                    )
                )
                + (TestCase.actual_result, TestCase.notes),
                "test_case:read",
                parent=TestDesign,
                container=TestCase.design_id,
                conditions=(TestDesign.deleted_at.is_(None),),
            ),
            source(
                "document",
                "document",
                ProjectDocument,
                ProjectDocument.title,
                literal(""),
                (ProjectDocument.body,),
                "document:read",
                conditions=(ProjectDocument.deleted_at.is_(None),),
            ),
        ).subquery()

    def search(
        self,
        db: Session,
        *,
        tenant_id: int,
        user_id: int,
        q: str,
        category: str | None,
        project_id: int | None,
        page: int,
        page_size: int,
    ) -> tuple[dict[str, int], int, list[dict]]:
        """同じ条件の件数集計と上限付き結果を取得する。"""
        rows = self.sources(tenant_id, user_id, q, project_id)
        counts: dict[str, int] = {
            category: count
            for category, count in db.execute(
                select(rows.c.category, func.count()).group_by(rows.c.category)
            ).all()
        }
        query = select(rows)
        if category:
            query = query.where(rows.c.category == category)
        items = (
            db.execute(
                query.order_by(rows.c.rank, rows.c.project_id, rows.c.kind, rows.c.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .mappings()
            .all()
        )
        total = counts.get(category, 0) if category else sum(counts.values())
        return counts, total, [dict(item) for item in items]

    def projects(
        self,
        db: Session,
        *,
        tenant_id: int,
        user_id: int,
        q: str,
        page: int,
        page_size: int,
        selected_id: int | None,
    ) -> tuple[int, list[dict], dict | None]:
        """内容を検索できる所属だけを候補・選択値に返す。"""
        base = select(Project.id, Project.name, Project.project_code).where(
            Project.id.in_(self.project_ids(tenant_id, user_id, READ_PERMISSIONS))
        )
        selected = None
        if selected_id is not None:
            selected = (
                db.execute(base.where(Project.id == selected_id)).mappings().first()
            )
        query = base
        if q:
            pattern = escaped_pattern(q)
            query = query.where(
                or_(
                    Project.name.ilike(pattern, escape="\\"),
                    Project.project_code.ilike(pattern, escape="\\"),
                )
            )
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        items = (
            db.execute(
                query.order_by(Project.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .mappings()
            .all()
        )
        return (
            total,
            [dict(item) for item in items],
            dict(selected) if selected else None,
        )
