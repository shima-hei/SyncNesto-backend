"""承認済みの開発用MCP読取。既存UIのMCP拒否ガードは解除しない。"""

from datetime import UTC, datetime

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.user import UserType
from app.repositories.mcp import McpRepository
from app.repositories.mcp_read import McpReadRepository
from app.schemas import mcp_read as s
from app.services.authorization import AuthorizationService

RELATION_PERMISSIONS = {
    "designs": "document:read",
    "tasks": "task:read",
    "tests": "test_plan:read",
    "documents": "document:read",
}


def read_page(total, page, page_size):
    """件数0と次ページがある状態を区別する。"""
    more = page * page_size < total
    return s.ReadPage(
        page=page,
        page_size=page_size,
        total=total,
        has_more=more,
        next_page=page + 1 if more else None,
    )


def reference(kind, project_id, entity_id, container_id=None):
    """公開済みツールだけを案内し、存在しない詳細操作を作らない。"""
    base = {"project_id": project_id}
    if kind in {"requirement", "requirement_document", "section"}:
        target = {
            "requirement": "requirement_item",
            "requirement_document": "document",
            "section": "section",
        }[kind]
        return s.ReadReference(
            tool="get_requirement_target",
            arguments={
                **base,
                "target_type": target,
                "target_id": int(entity_id),
            },
        )
    if kind == "task":
        return s.ReadReference(
            tool="get_task", arguments={**base, "task_id": int(entity_id)}
        )
    if kind in {"test_design", "test_item"}:
        design_id = int(entity_id) if kind == "test_design" else container_id
        return s.ReadReference(
            tool="get_test_design",
            arguments={
                **base,
                "design_id": design_id,
                "component": "items",
            },
        )
    if kind == "document":
        return s.ReadReference(
            tool="get_project_document_content",
            arguments={
                **base,
                "document_id": int(entity_id),
                "mode": "outline",
            },
        )
    return None


class McpReadService:
    """外側で同意Project・接続資格を検証し、各関連のreadを再確認する。"""

    def __init__(self):
        """既存の業務認可とデータアクセスを再利用する。"""
        self.repository = McpReadRepository()
        self.projects = McpRepository()
        self.authorization = AuthorizationService()

    def can(self, db, user, project_id, permission):
        """取得しない分野の件数や存在を漏らさない。"""
        return self.authorization.has_project_permission(
            db, user=user, project_id=project_id, permission_code=permission
        )

    def read(self, db, user, name, data):
        """追加3操作の成功結果を公開outputSchemaで検証する。"""
        if (
            user.user_type != UserType.INTERNAL.value
            or db.info.get("demo_id")
            or db.info.get("data_realm") == "demo"
        ):
            raise ForbiddenError()
        if name == "search_project_items":
            result = self.search(db, user, data)
        elif name == "get_requirement_context":
            result = self.context(db, user, data)
        elif name == "get_project_brief":
            result = self.brief(db, user, data)
        else:
            raise NotFoundError()
        return result.model_dump(mode="json")

    def search(self, db, user, data):
        """8種の検索を同じ案件内に固定し、本文全件を返さない。"""
        project = self.projects.project(db, data.project_id)
        if project is None:
            raise NotFoundError()
        total, rows = self.repository.search_items(
            db,
            project,
            user.id,
            data,
            include_objectives=settings.project_objectives_enabled,
        )
        items = [
            s.SearchMatch(
                entity_type=row["kind"],
                id=row["id"],
                project_id=project.id,
                code=row["code"],
                title=row["title"],
                status=row["status"],
                version=row["version"],
                updated_at=row["updated_at"],
                container_id=row["container_id"],
                excerpt=row["excerpt"],
                read=reference(row["kind"], project.id, row["id"], row["container_id"]),
            )
            for row in rows
        ]
        warnings = [
            "only_currently_readable_matching_records; hidden_records_not_counted",
            "live_pages_may_change; "
            "ranking=exact_title,title_match,code_match,body_match; ties=type,id",
        ]
        if data.status is not None:
            warnings.append("status_filter_excludes_types_without_stored_status")
        if any(item.read is None for item in items):
            warnings.append("some_types_have_no_mcp_detail_tool; do_not_invent_one")
        return s.ProjectSearchRead(
            project_id=project.id,
            items=items,
            pagination=read_page(total, data.page, data.page_size),
            warnings=warnings,
        )

    def context(self, db, user, data):
        """要件本文とIDで結び付いた関連を取得する。"""
        text_limit = {"summary": 0, "standard": 2000, "full": 8000}[data.detail_level]
        row = self.repository.requirement(
            db, data.project_id, data.requirement_id, text_limit
        )
        if row is None:
            raise NotFoundError()
        pid = data.project_id
        entity = s.ReadEntity(
            entity_type="requirement",
            id=str(row["id"]),
            project_id=pid,
            code=row["code"],
            title=row["title"],
            status=row["status"],
            version=row["version"],
            updated_at=row["updated_at"],
            container_id=row["document_id"],
            read=reference("requirement", pid, row["id"]),
        )
        document = s.ReadEntity(
            entity_type="requirement_document",
            id=str(row["document_id"]),
            project_id=pid,
            code=row["document_code"],
            title=row["document_title"],
            version=row["document_version"],
            updated_at=row["document_updated_at"],
            read=reference("requirement_document", pid, row["document_id"]),
        )
        section = (
            s.ReadEntity(
                entity_type="section",
                id=str(row["section_id"]),
                project_id=pid,
                title=row["section_title"],
                version=row["section_version"],
                updated_at=row["section_updated_at"],
                container_id=row["document_id"],
                read=reference("section", pid, row["section_id"]),
            )
            if row["section_id"] is not None
            else None
        )
        content = s.RequirementContent(
            state="loaded" if text_limit else "not_requested"
        )
        if text_limit:
            for field in ("description", "acceptance_criteria", "rationale"):
                value = row[field]
                setattr(
                    content, field, value[:text_limit] if value is not None else None
                )
                if value is not None and len(value) > text_limit:
                    content.truncated_fields.append(field)
        relations = {}
        for kind, permission in RELATION_PERMISSIONS.items():
            if kind not in data.include:
                relations[kind] = s.ReadCollection(state="not_requested")
            elif not self.can(db, user, pid, permission):
                relations[kind] = s.ReadCollection(state="unavailable")
            elif not settings.project_traceability_enabled:
                relations[kind] = s.ReadCollection(state="disabled")
            else:
                total, targets = self.repository.related(
                    db,
                    pid,
                    row["id"],
                    kind,
                    data.relation_page,
                    data.relation_page_size,
                )
                target_kind = {
                    "designs": "specification",
                    "tasks": "task",
                    "tests": "test_item",
                    "documents": "document",
                }[kind]
                items = [
                    s.ReadEntity(
                        entity_type=target_kind,
                        id=str(target["target_id"]),
                        project_id=pid,
                        code=target["code"],
                        title=target["title"],
                        status=target.get("status"),
                        version=target.get("version") if kind != "tests" else None,
                        updated_at=target["updated_at"],
                        container_id=target.get("design_id"),
                        container_version=target.get("version")
                        if kind == "tests"
                        else None,
                        read=reference(
                            target_kind,
                            pid,
                            target["target_id"],
                            target.get("design_id"),
                        ),
                    )
                    for target in targets
                ]
                relations[kind] = s.ReadCollection(
                    state="loaded",
                    items=items,
                    pagination=read_page(
                        total, data.relation_page, data.relation_page_size
                    ),
                )
        return s.RequirementContextRead(
            entity=entity,
            document=document,
            section=section,
            content=content,
            relations=relations,
            review=s.ReadCollection(
                state="not_requested",
                read=s.ReadReference(
                    tool="list_requirement_comments",
                    arguments={
                        "project_id": pid,
                        "target_type": "requirement_item",
                        "target_id": row["id"],
                    },
                ),
            ),
            warnings=[
                "current_records_not_fixed_approval_or_requirement_verdict",
                "relations_use_saved_foreign_keys_not_identifiers_in_prose",
                "direct_links_only; child_and_transitive_links_not_expanded",
                "live_read_not_atomic_snapshot",
            ],
        )

    def objectives(self, db, user, project):
        """同意済み案件の、選択された同案件文書だけを既存read権限で読む。"""
        if not self.can(db, user, project.id, "requirement:read"):
            return s.ObjectiveContext(state="unavailable")
        if not settings.project_objectives_enabled:
            return s.ObjectiveContext(state="disabled")
        if project.goal_document_id is None:
            return s.ObjectiveContext(
                state="loaded",
                source_state="unselected",
                source_version=project.goal_source_version,
            )
        document = self.repository.objective(db, project.id, project.goal_document_id)
        if document is None:
            return s.ObjectiveContext(
                state="loaded",
                source_state="unavailable",
                source_version=project.goal_source_version,
            )
        values, truncated = {}, []
        for name in ("goal", "scope", "completion_criteria"):
            value = document[name]
            values[name] = value[:2000] if value is not None else None
            if value is not None and len(value) > 2000:
                truncated.append(name)
        return s.ObjectiveContext(
            state="loaded",
            source_state="selected",
            source_version=project.goal_source_version,
            document=s.ReadEntity(
                entity_type="requirement_document",
                id=str(document["id"]),
                project_id=project.id,
                code=document["document_code"],
                title=document["title"],
                version=document["version"],
                updated_at=document["updated_at"],
                read=reference("requirement_document", project.id, document["id"]),
            ),
            **values,
            truncated_fields=truncated,
        )

    def brief(self, db, user, data):
        """存在しない工程・未集計の承認を推測で埋めない。"""
        project = self.projects.project(db, data.project_id)
        if project is None:
            raise NotFoundError()
        objectives = (
            self.objectives(db, user, project)
            if "objectives" in data.include
            else s.ObjectiveContext(state="not_requested")
        )
        tasks = s.TaskCounts(state="not_requested")
        milestones = s.ReadCollection(state="not_requested")
        if "tasks" in data.include or "milestones" in data.include:
            allowed = self.can(db, user, project.id, "task:read")
            if "tasks" in data.include:
                if not allowed:
                    tasks = s.TaskCounts(state="unavailable")
                else:
                    as_of = data.as_of_date or datetime.now(UTC).date()
                    total, overdue, statuses = self.repository.task_counts(
                        db, project.id, as_of
                    )
                    tasks = s.TaskCounts(
                        state="loaded",
                        total=total,
                        by_status=dict(statuses[:100]),
                        statuses_truncated=len(statuses) > 100,
                        overdue=overdue,
                        as_of_date=as_of,
                        basis="non_deleted_tasks_in_project; "
                        "includes_parent_and_child_rows; "
                        "stored_status; overdue=due_date<as_of_date "
                        "AND status NOT IN(done,cancelled); date="
                        + ("caller" if data.as_of_date else "UTC"),
                    )
            if "milestones" in data.include:
                if not allowed:
                    milestones = s.ReadCollection(state="unavailable")
                else:
                    total, rows = self.repository.milestones(db, project.id, data.limit)
                    milestones = s.ReadCollection(
                        state="loaded",
                        items=[
                            s.ReadEntity(
                                entity_type="milestone",
                                id=str(row["target_id"]),
                                project_id=project.id,
                                title=row["title"],
                                status=row["status"],
                                version=row["version"],
                                updated_at=row["updated_at"],
                                target_date=row["target_date"],
                            )
                            for row in rows
                        ],
                        pagination=read_page(total, 1, data.limit),
                    )
        description = project.description
        return s.ProjectBriefRead(
            entity=s.ReadEntity(
                entity_type="project",
                id=str(project.id),
                project_id=project.id,
                code=project.project_code,
                title=project.name,
                status=project.status,
                version=project.version,
                updated_at=project.updated_at,
            ),
            description=description[:2000] if description is not None else None,
            description_truncated=description is not None and len(description) > 2000,
            objectives=objectives,
            tasks=tasks,
            milestones=milestones,
            warnings=[
                "current_phase_not_stored; no_phase_inferred_from_names",
                "reviews_approvals_and_change_history_not_aggregated",
                "live_read_not_atomic_snapshot",
            ],
        )
