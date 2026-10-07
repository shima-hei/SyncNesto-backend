"""削除された資源の検索と、所有経路に沿う完全削除計画。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    String,
    cast,
    delete,
    func,
    literal,
    or_,
    select,
    union_all,
    update,
)
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.tenant_scope import MENTION_OWNERS, OWNERS
from app.models.demo import DemoSession
from app.models.document import DocumentAttachment, ProjectDocument
from app.models.project import Project
from app.models.requirement import Requirement, RequirementDocument, RequirementSection
from app.models.task import Task
from app.models.test_design import TestDesign


@dataclass(frozen=True)
class TrashResource:
    """既存モデルと閲覧・操作権限、親の対応。"""

    model: Any
    title: str
    permission: str
    operation: str = "delete"
    parent: Any = None
    parent_key: str | None = None


RESOURCES = {
    "requirement_document": TrashResource(RequirementDocument, "title", "requirement"),
    "requirement_section": TrashResource(
        RequirementSection,
        "title",
        "requirement",
        parent=RequirementDocument,
        parent_key="document_id",
    ),
    "requirement": TrashResource(
        Requirement,
        "title",
        "requirement",
        parent=RequirementDocument,
        parent_key="document_id",
    ),
    "task": TrashResource(Task, "title", "task"),
    "test_design": TrashResource(TestDesign, "name", "test_plan"),
    "document": TrashResource(ProjectDocument, "title", "document"),
    "document_attachment": TrashResource(
        DocumentAttachment,
        "filename",
        "document",
        operation="update",
        parent=ProjectDocument,
        parent_key="document_id",
    ),
}


class TrashRepository:
    """全種類をページングしてから取得し、本文はロードしない。"""

    def due(
        self, db: Session, tenant_id: int, cutoff: datetime, limit: int
    ) -> list[dict]:
        """専用デモと削除済みProjectを除き、組織内の候補を制限する。"""
        queries = []
        for kind, spec in RESOURCES.items():
            model = spec.model
            statement = select(
                literal(kind).label("kind"),
                model.id.cast(String).label("id"),
                Project.id.label("project_id"),
                model.deleted_at,
            )
            if spec.parent is not None:
                statement = statement.join(
                    spec.parent, getattr(model, spec.parent_key or "") == spec.parent.id
                )
                project_id = spec.parent.project_id
            else:
                project_id = model.project_id
            statement = statement.join(Project, project_id == Project.id).where(
                Project.tenant_id == tenant_id,
                Project.deleted_at.is_(None),
                model.deleted_at <= cutoff,
                ~select(DemoSession.id)
                .where(DemoSession.tenant_id == tenant_id)
                .exists(),
            )
            queries.append(statement)
        candidates = union_all(*queries).subquery()
        return [
            dict(row)
            for row in db.execute(
                select(candidates)
                .order_by(candidates.c.deleted_at, candidates.c.kind, candidates.c.id)
                .limit(limit)
            ).mappings()
        ]

    def storage_keys(self, db: Session, targets: dict[str, set]) -> list[str]:
        """回収する文書添付・証跡の確定済みキーだけを返す。"""
        keys = []
        for name in ("document_attachments", "test_evidence"):
            if not targets.get(name):
                continue
            table = Base.metadata.tables[name]
            keys.extend(
                db.connection().scalars(
                    select(table.c.storage_key).where(table.c.id.in_(targets[name]))
                )
            )
        return keys

    def list(
        self,
        db: Session,
        project_id: int,
        kinds: list[str],
        q: str,
        page: int,
        page_size: int,
    ) -> tuple[list[dict], int]:
        """削除日順の概要を一つのSQL集合から返す。"""
        queries = []
        for kind in kinds:
            spec = RESOURCES[kind]
            model = spec.model
            parent = spec.parent
            project = parent.project_id if parent is not None else model.project_id
            statement = select(
                literal(kind).label("kind"),
                cast(model.id, String).label("id"),
                getattr(model, spec.title).label("title"),
                model.deleted_at,
                getattr(model, "version", literal(None)).label("version"),
                getattr(model, spec.parent_key or "").label("container_id")
                if parent is not None
                else literal(None).label("container_id"),
                parent.deleted_at.label("parent_deleted_at")
                if parent is not None
                else literal(None).label("parent_deleted_at"),
            ).where(project == project_id, model.deleted_at.is_not(None))
            if parent is not None:
                statement = statement.join(
                    parent, getattr(model, spec.parent_key or "") == parent.id
                )
            if q:
                escaped = (
                    q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                )
                statement = statement.where(
                    getattr(model, spec.title).ilike(f"%{escaped}%", escape="\\")
                )
            queries.append(statement)
        if not queries:
            return [], 0
        rows = union_all(*queries).subquery()
        total = db.scalar(select(func.count()).select_from(rows)) or 0
        statement = select(rows).order_by(
            rows.c.deleted_at.desc(), rows.c.kind, rows.c.id
        )
        items = db.execute(
            statement.offset((page - 1) * page_size).limit(page_size)
        ).mappings()
        return [dict(row) for row in items], total

    def get(
        self, db: Session, project_id: int, kind: str, resource_id: str
    ) -> tuple[Any, Any]:
        """親からロックし、所有Project内の行を最新状態で取得する。"""
        spec = RESOURCES[kind]
        model = spec.model
        try:
            identity = (
                UUID(resource_id) if model is DocumentAttachment else int(resource_id)
            )
        except ValueError:
            return None, None
        if isinstance(identity, int) and not 1 <= identity <= 2_147_483_647:
            return None, None
        if spec.parent is not None:
            parent_id = db.scalar(
                select(getattr(model, spec.parent_key or "")).where(
                    model.id == identity
                )
            )
            parent = db.scalar(
                select(spec.parent)
                .where(
                    spec.parent.id == parent_id,
                    spec.parent.project_id == project_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if parent is None:
                return None, None
            condition = getattr(model, spec.parent_key or "") == parent.id
        else:
            parent = None
            condition = getattr(model, "project_id") == project_id
        row = db.scalar(
            select(model)
            .where(model.id == identity, condition)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return row, parent

    def purge_plan(
        self, db: Session, table_name: str, identity: object
    ) -> dict[str, set]:
        """所有する子と関連行を確定する。業務資源の関連先は削除しない。"""
        tables = Base.metadata.tables
        targets: dict[str, set] = {table_name: {identity}}
        edges = dict(OWNERS)
        edges["document_links"] = edges["document_links"] + [
            ("requirement_id", "requirements", "id"),
            ("task_id", "tasks", "id"),
            ("design_id", "test_designs", "id"),
        ]
        edges["requirement_relations"] = edges["requirement_relations"] + [
            ("source_requirement_id", "requirements", "id")
        ]
        edges["comment_mentions"] = [(c, p, "id") for c, p in MENTION_OWNERS.items()]
        changed = True
        while changed:
            changed = False
            for name, parents in edges.items():
                conditions = [
                    tables[name].c[c].in_(targets[p])
                    for c, p, _ in parents
                    if targets.get(p)
                ]
                if not conditions:
                    continue
                key = next(iter(tables[name].primary_key.columns))
                found = set(
                    db.connection().scalars(select(key).where(or_(*conditions)))
                )
                previous = targets.setdefault(name, set())
                if found - previous:
                    previous.update(found)
                    changed = True
            # 型付きIDで参照する履歴、承認、コメントも本文と一緒に回収する。
            for parent_name, target_type in (
                ("requirements", "requirement"),
                ("requirement_sections", "section"),
                ("tasks", "task"),
            ):
                if not targets.get(parent_name):
                    continue
                names = (
                    ("task_change_logs",)
                    if target_type == "task"
                    else (
                        "requirement_change_logs",
                        "requirement_approvals",
                        "requirement_target_comments",
                    )
                )
                for name in names:
                    table = tables[name]
                    found = set(
                        db.connection().scalars(
                            select(table.c.id).where(
                                table.c.target_type == target_type,
                                table.c.target_id.in_(targets[parent_name]),
                            )
                        )
                    )
                    previous = targets.setdefault(name, set())
                    if found - previous:
                        previous.update(found)
                        changed = True
        return targets

    def detachments(
        self, db: Session, targets: dict[str, set]
    ) -> list[tuple[Any, Any, Any]]:
        """想定外の外部キー参照は、ストレージ削除より前に処理を拒否する。"""
        allowed = {
            ("tasks", "parent_task_id"),
            ("requirements", "section_id"),
            ("requirement_open_issues", "related_requirement_id"),
        }
        result = []
        for table in Base.metadata.sorted_tables:
            for column in table.c:
                for fk in column.foreign_keys:
                    parent = fk.column.table.name
                    ids = targets.get(parent)
                    if not ids or fk.column.name != "id":
                        continue
                    condition = column.in_(ids)
                    key = next(iter(table.primary_key.columns))
                    if targets.get(table.name):
                        condition &= key.not_in(targets[table.name])
                    if not db.connection().scalar(
                        select(key).where(condition).limit(1)
                    ):
                        continue
                    if (table.name, column.name) not in allowed:
                        raise RuntimeError(
                            f"Unclassified purge reference: {table.name}.{column.name}"
                        )
                    result.append((table, column, condition))
        return result

    def purge(
        self, db: Session, targets: dict[str, set], detachments: list[tuple]
    ) -> None:
        """生存する子の参照を解除し、確定した子から削除する。"""
        for table, column, condition in detachments:
            values = {column.name: None}
            if "version" in table.c:
                values["version"] = table.c.version + 1
            db.connection().execute(update(table).where(condition).values(**values))
        for table in reversed(Base.metadata.sorted_tables):
            ids = targets.get(table.name)
            if not ids:
                continue
            condition = next(iter(table.primary_key.columns)).in_(ids)
            self_refs = {
                c.name: None
                for c in table.c
                if c.nullable and any(f.column.table == table for f in c.foreign_keys)
            }
            if self_refs:
                db.connection().execute(
                    update(table).where(condition).values(**self_refs)
                )
            db.connection().execute(delete(table).where(condition))
