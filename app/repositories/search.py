"""組織・所属・種類ごとの権限をSQLで適用する横断検索。"""

from collections import defaultdict
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    Integer,
    Select,
    String,
    and_,
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

from app.models.document import DocumentAttachment, ProjectDocument
from app.models.project import Project, ProjectMember
from app.models.rbac import Permission, Role, RolePermission
from app.models.requirement import Requirement, RequirementDocument
from app.models.specification import Specification
from app.models.task import Task
from app.models.test_design import TestCase, TestDesign, TestItem
from app.schemas.smart_link import UUID_KINDS, SmartLinkReference

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
        self,
        tenant_id: int,
        user_id: int,
        q: str,
        project_id: int | None,
        *,
        include_objectives: bool = False,
        include_metadata: bool = False,
        smart_link: bool = False,
        references: dict[str, list[SmartLinkReference]] | None = None,
        preview: bool = False,
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
            if references is not None and kind not in references:
                return None
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
                (literal("") if smart_link else func.substr(body, start, 240)).label(
                    "excerpt"
                ),
                rank.label("rank"),
                *(
                    [
                        cast(
                            Specification.kind
                            if model is Specification
                            else literal(None),
                            String,
                        ).label("specification_kind")
                    ]
                    if smart_link
                    else []
                ),
                *(self.preview_columns(model) if preview else []),
                *(
                    [
                        cast(getattr(model, "status", literal(None)), String).label(
                            "status"
                        ),
                        cast(getattr(model, "version", literal(None)), Integer).label(
                            "version"
                        ),
                        cast(
                            getattr(model, "updated_at", literal(None)),
                            DateTime(timezone=True),
                        ).label("updated_at"),
                    ]
                    if include_metadata
                    else []
                ),
            ).select_from(model)
            if parent is not None:
                assert container is not None
                query = query.join(parent, container == parent.id)
            owner = parent if parent is not None else model
            if model is not Project:
                query = query.join(Project, owner.project_id == Project.id)
            query = query.where(
                Project.id.in_(self.project_ids(tenant_id, user_id, (permission,))),
                *conditions,
            )
            if references is not None:
                query = query.where(
                    or_(
                        *(
                            and_(
                                Project.id == ref.project_id,
                                model.id
                                == (
                                    UUID(ref.id) if kind in UUID_KINDS else int(ref.id)
                                ),
                                *(
                                    (container == ref.container_id,)
                                    if container is not None
                                    else ()
                                ),
                            )
                            for ref in references[kind]
                        )
                    )
                )
            else:
                matches = [
                    title.ilike(pattern, escape="\\"),
                    code.ilike(pattern, escape="\\"),
                ]
                if not smart_link:
                    matches.append(body.ilike(pattern, escape="\\"))
                query = query.where(or_(*matches))
            if project_id is not None:
                query = query.where(Project.id == project_id)
            return query

        queries = [
            source(
                "requirement_document",
                "requirement",
                RequirementDocument,
                RequirementDocument.title,
                RequirementDocument.document_code,
                (RequirementDocument.purpose, RequirementDocument.target_system_name)
                + (
                    (
                        RequirementDocument.project_goal,
                        RequirementDocument.project_scope,
                        RequirementDocument.project_completion_criteria,
                    )
                    if include_objectives
                    else ()
                ),
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
            source(
                "specification",
                "document",
                Specification,
                Specification.title,
                literal(""),
                (Specification.summary, cast(Specification.content, String)),
                "document:read",
                conditions=(Specification.deleted_at.is_(None),),
            ),
        ]
        if smart_link:
            queries.extend(
                [
                    source(
                        "project",
                        "project",
                        Project,
                        Project.name,
                        Project.project_code,
                        (),
                        "project:read",
                        conditions=(Project.deleted_at.is_(None),),
                    ),
                    source(
                        "document_attachment",
                        "document",
                        DocumentAttachment,
                        DocumentAttachment.filename,
                        literal(""),
                        (),
                        "document:read",
                        parent=ProjectDocument,
                        container=DocumentAttachment.document_id,
                        conditions=(
                            DocumentAttachment.deleted_at.is_(None),
                            ProjectDocument.deleted_at.is_(None),
                        ),
                    ),
                ]
            )
        return union_all(*(query for query in queries if query is not None)).subquery()

    @staticmethod
    def preview_columns(model):
        """概要を求められた場合だけ、既存属性を取得する。"""
        person = {
            Task: Task.assignee_id,
            Requirement: Requirement.owner_id,
            TestCase: TestCase.executed_by,
        }.get(model, literal(None))
        count = (
            select(func.count(TestItem.id))
            .where(
                TestItem.design_id == TestDesign.id,
                TestItem.deleted_at.is_(None),
                TestItem.is_spacer.is_(False),
            )
            .correlate(TestDesign)
            .scalar_subquery()
            if model is TestDesign
            else literal(None)
        )
        return [
            cast(getattr(model, "status", literal(None)), String).label("status"),
            cast(getattr(model, "priority", literal(None)), String).label("priority"),
            cast(getattr(model, "requirement_type", literal(None)), String).label(
                "requirement_type"
            ),
            cast(person, Integer).label("assignee_id"),
            cast(getattr(model, "due_date", literal(None)), Date).label("due_date"),
            cast(
                getattr(
                    model, "updated_at", getattr(model, "created_at", literal(None))
                ),
                DateTime(timezone=True),
            ).label("updated_at"),
            cast(getattr(model, "content_type", literal(None)), String).label(
                "file_type"
            ),
            cast(count, Integer).label("test_item_count"),
            cast(
                func.substr(TestCase.actual_result, 1, 240)
                if model is TestCase
                else literal(None),
                String,
            ).label("actual_result"),
        ]

    def resolve_links(
        self,
        db: Session,
        *,
        tenant_id: int,
        user_id: int,
        references: list[SmartLinkReference],
        preview: bool,
    ) -> list[dict]:
        """案件・成果物・親IDを指定して検索と同じ権限で解決する。"""
        by_kind: dict[str, list[SmartLinkReference]] = defaultdict(list)
        for ref in references:
            by_kind[ref.kind].append(ref)
        rows = self.sources(
            tenant_id,
            user_id,
            "",
            None,
            smart_link=True,
            references=by_kind,
            preview=preview,
        )
        return [dict(row) for row in db.execute(select(rows)).mappings()]

    def find_links(
        self,
        db: Session,
        *,
        tenant_id: int,
        user_id: int,
        q: str,
        group: str | None,
        preferred_project: int | None,
    ) -> list[dict]:
        """参照先をタイトル・コードで検索し、作業中の案件を優先する。"""
        rows = self.sources(tenant_id, user_id, q, None, smart_link=True)
        query = select(rows)
        if group == "specification":
            query = query.where(rows.c.kind == "specification")
        elif group is not None:
            query = query.where(
                rows.c.category == group, rows.c.kind != "specification"
            )
        preferred_rank = case((rows.c.project_id == preferred_project, 0), else_=1)
        query = query.order_by(
            preferred_rank, rows.c.rank, rows.c.project_id, rows.c.kind, rows.c.id
        ).limit(16)
        return [dict(row) for row in db.execute(query).mappings()]

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
