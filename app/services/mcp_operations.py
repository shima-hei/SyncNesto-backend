"""MCP操作を既存業務Serviceへ渡す。認可と再送防止は毎回検証する。"""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import cast

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core import error_messages as messages
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    VersionConflictError,
)
from app.models.mcp import McpConnection
from app.models.user import User
from app.repositories.mcp import McpRepository
from app.schemas import mcp_tools as s
from app.schemas.requirement import RequirementTargetCommentCreate
from app.schemas.task import TaskUpdate
from app.schemas.test_design import TestDesignUpdate
from app.services.audit_log import AuditLogService
from app.services.mcp_auth import McpAuthService
from app.services.mcp_catalog import TOOLS
from app.services.requirement_document import RequirementDocumentService
from app.services.requirement_item import RequirementService
from app.services.requirement_section import RequirementSectionService
from app.services.requirement_target_comment import RequirementTargetCommentService
from app.services.task import TaskService
from app.services.task_dependency import TaskDependencyService
from app.services.task_milestone import TaskMilestoneService
from app.services.test_collaboration import TestCollaborationService
from app.services.test_design import TestDesignService


def request_digest(value: dict) -> str:
    """順序の異なるJSONを同じ入力として扱う。"""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def summary(row) -> dict:
    """一覧・書き込み結果には本文を含めない。"""
    fields = (
        "id",
        "project_id",
        "document_id",
        "version",
        "title",
        "name",
        "status",
        "task_code",
        "requirement_code",
        "document_code",
        "parent_task_id",
        "assignee_id",
        "start_date",
        "due_date",
        "estimated_minutes",
    )
    return json.loads(
        json.dumps(
            {key: getattr(row, key) for key in fields if hasattr(row, key)}, default=str
        )
    )


class McpOperationsService:
    """接続範囲∩現在のユーザー権限で操作する。"""

    def __init__(self) -> None:
        """既存Serviceを再利用する。"""
        self.auth = McpAuthService()
        self.repository = McpRepository()
        self.designs = TestDesignService()
        self.tasks = TaskService()

    def allowed(
        self,
        db: Session,
        connection: McpConnection,
        user: User,
        project_id: int,
        permissions: tuple[str, ...],
    ) -> bool:
        """UIやツール一覧の表示を信用せず現在の権限を確認する。"""
        return (
            project_id in connection.project_ids
            and self.auth.eligible(db, user, project_id)
            and all(
                self.auth.authorization.has_project_permission(
                    db, user=user, project_id=project_id, permission_code=permission
                )
                for permission in permissions
            )
        )

    def catalog(self, db: Session, connection: McpConnection, user: User) -> list[dict]:
        """少なくとも一つの許可Projectで実行できるツールのみ提示する。"""
        return [
            {
                "name": name,
                "description": tool.description,
                "inputSchema": tool.schema.model_json_schema(),
                "annotations": {
                    "readOnlyHint": not tool.write or tool.preview,
                    "destructiveHint": tool.write and not tool.preview,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            }
            for name, tool in TOOLS.items()
            if not tool.permissions
            or any(
                self.allowed(db, connection, user, project_id, tool.permissions)
                for project_id in connection.project_ids
            )
        ]

    def execute(
        self,
        db: Session,
        connection: McpConnection,
        user: User,
        operation: s.McpOperation,
    ) -> dict:
        """認可後にロックし、業務変更・監査・receiptを同時に確定する。"""
        tool = TOOLS.get(operation.tool)
        if tool is None:
            raise NotFoundError()
        if operation.tool == "list_projects":
            if operation.arguments:
                raise BadRequestError(messages.MCP_INVALID_INPUT)
            return {
                "items": [
                    summary(self.repository.project(db, project_id))
                    for project_id in connection.project_ids
                    if self.auth.eligible(db, user, project_id)
                ]
            }
        try:
            data = cast(s.WriteInput, tool.schema.model_validate(operation.arguments))
        except ValidationError as exc:
            raise BadRequestError(messages.MCP_INVALID_INPUT) from exc
        project_id = data.project_id
        if not self.allowed(db, connection, user, project_id, tool.permissions):
            raise ForbiddenError()
        if (
            isinstance(data, s.TaskCreateInput)
            and data.data.requirement_id is not None
            and not self.auth.authorization.has_project_permission(
                db, user=user, project_id=project_id, permission_code="requirement:read"
            )
        ):
            raise ForbiddenError()
        if not tool.write:
            return self.read(db, operation.tool, data)
        key = data.idempotency_key
        self.repository.lock_operation(db, connection.id, key)
        connection, user = self.auth.valid_connection(db, connection.id)
        if not self.allowed(db, connection, user, project_id, tool.permissions):
            raise ForbiddenError()
        hashed = request_digest(
            {
                "tool": operation.tool,
                "arguments": data.model_dump(mode="json", exclude_unset=True),
            }
        )
        receipt = self.repository.receipt(db, connection.id, key)
        if receipt is not None:
            if receipt.request_hash != hashed:
                raise ConflictError(messages.MCP_KEY_REUSED)
            return receipt.result
        result = self.write(db, connection, user, operation.tool, data)
        self.repository.save_receipt(db, connection.id, key, hashed, result)
        # MCPでは監査失敗も書き込み失敗にし、外側のtransactionで全て戻す。
        AuditLogService().repository.create(
            db,
            event_type="mcp.operation",
            actor_user_id=user.id,
            project_id=project_id,
            resource_type="mcp_connection",
            resource_id=None,
            metadata={
                "source": "mcp",
                "connection_id": str(connection.id),
                "operation": operation.tool,
            },
        )
        return result

    def target(
        self,
        db: Session,
        project_id: int,
        target_type: str,
        target_id: int,
        *,
        lock: bool = False,
    ):
        """別Project・削除済みの対象を除く。"""
        row = self.repository.target(db, project_id, target_type, target_id, lock=lock)
        if row is None:
            raise NotFoundError()
        return row

    def task(self, db: Session, project_id: int, task_id: int):
        """同じ組織でも別Projectのタスクは操作しない。"""
        task = self.tasks.get_task(db, task_id)
        if task.project_id != project_id:
            raise NotFoundError()
        return task

    @staticmethod
    def version(row, version: int) -> None:
        """巨大な本文をエラーへ載せず競合を返す。"""
        if row.version != version:
            raise VersionConflictError(summary(row))

    def page(self, rows: list, data: s.PageInput, total: int | None = None) -> dict:
        """ページ情報を返す。"""
        return {
            "items": [summary(row) for row in rows],
            "total": total if total is not None else len(rows),
            "page": data.page,
            "page_size": data.page_size,
        }

    def read(self, db: Session, name: str, data) -> dict:
        """参照結果を小さく保ち、必要な本文は明示的に取得する。"""
        if name in {
            "list_requirement_sections",
            "list_requirement_comments",
            "list_test_design_comments",
            "list_task_dependencies",
            "list_milestones",
        }:
            if name == "list_requirement_sections":
                rows = RequirementSectionService().list_sections(
                    db, project_id=data.project_id, document_id=data.document_id
                )
            elif name == "list_requirement_comments":
                from app.schemas.requirement import RequirementTargetCommentRead

                rows = [
                    RequirementTargetCommentRead.model_validate(row)
                    for row in RequirementTargetCommentService().list_comments(
                        db,
                        project_id=data.project_id,
                        target_type=data.target_type,
                        target_id=data.target_id,
                    )
                ]
            elif name == "list_test_design_comments":
                rows = TestCollaborationService().list_comments(
                    db, data.project_id, data.design_id
                )
            elif name == "list_task_dependencies":
                from app.schemas.task import TaskDependencyRead

                self.task(db, data.project_id, data.task_id)
                rows = [
                    TaskDependencyRead.model_validate(row)
                    for row in TaskDependencyService().list_dependencies(
                        db, data.task_id
                    )
                ]
            else:
                from app.schemas.task import MilestoneRead

                rows = [
                    MilestoneRead.model_validate(row)
                    for row in TaskMilestoneService().list_milestones(
                        db, data.project_id
                    )
                ]
            start = (data.page - 1) * data.page_size
            return {
                "items": [
                    row.model_dump(mode="json")
                    if isinstance(row, BaseModel)
                    else summary(row)
                    for row in rows[start : start + data.page_size]
                ],
                "total": len(rows),
                "page": data.page,
                "page_size": data.page_size,
            }
        if name == "get_project_context":
            return {
                **summary(self.repository.project(db, data.project_id)),
                "members": [
                    {"id": row.id, "name": row.name}
                    for row in self.repository.members(db, data.project_id)
                ],
                "scheduling": {
                    "dependency_type": "finish_to_start",
                    "holiday_calendar": None,
                    "capacity": None,
                },
            }
        if name == "list_requirement_documents":
            rows, total = RequirementDocumentService().list_documents_paginated(
                db, project_id=data.project_id, page=data.page, page_size=data.page_size
            )
            return self.page(rows, data, total)
        if name == "list_requirements":
            rows, total = RequirementService().list_requirements_paginated(
                db,
                project_id=data.project_id,
                document_id=data.document_id,
                page=data.page,
                page_size=data.page_size,
            )
            return self.page(rows, data, total)
        if name == "get_requirement_target":
            row = self.target(db, data.project_id, data.target_type, data.target_id)
            from app.schemas.requirement import (
                RequirementDocumentRead,
                RequirementOpenIssueRead,
                RequirementRead,
                RequirementSectionRead,
            )

            schema = {
                "document": RequirementDocumentRead,
                "section": RequirementSectionRead,
                "requirement_item": RequirementRead,
                "open_issue": RequirementOpenIssueRead,
            }[data.target_type]
            result = schema.model_validate(row).model_dump(mode="json")
            if data.target_type == "document":
                result["sections"] = [
                    summary(section)
                    for section in RequirementSectionService().list_sections(
                        db, project_id=data.project_id, document_id=data.target_id
                    )
                ][:100]
            return result
        if name == "list_test_designs":
            rows = self.designs.list_designs(db, data.project_id)
            start = (data.page - 1) * data.page_size
            return {
                "items": [
                    row.model_dump(mode="json")
                    for row in rows[start : start + data.page_size]
                ],
                "total": len(rows),
            }
        if name == "get_test_design":
            design = self.designs.get(db, data.project_id, data.design_id)
            graph = self.designs.read(db, design).model_dump(
                mode="json", include=set(TestDesignUpdate.model_fields)
            )
            start = (data.page - 1) * data.page_size
            rows = graph[data.component]
            return {
                **summary(design),
                "description": design.description,
                "component": data.component,
                "items": rows[start : start + data.page_size],
                "total": len(rows),
            }
        if name == "list_tasks":
            rows, total = self.tasks.list_tasks(db, **data.model_dump())
            return self.page(rows, data, total)
        if name == "get_task":
            row = self.task(db, data.project_id, data.task_id)
            return {
                **summary(row),
                **{
                    field: getattr(row, field)
                    for field in (
                        "description",
                        "task_type",
                        "priority",
                        "progress_percent",
                        "tags",
                    )
                },
            }
        raise NotFoundError()

    def write(
        self, db: Session, connection: McpConnection, user: User, name: str, data
    ) -> dict:
        """操作ごとの業務検証は既存Serviceに委譲する。"""
        project_id, actor_id = data.project_id, user.id
        if name in {
            "create_requirement_document",
            "create_requirement_section",
            "create_requirement",
        }:
            if (
                data.data.status != "draft"
                or getattr(data.data, "approved_by", None) is not None
                or getattr(data.data, "approved_at", None) is not None
            ):
                raise BadRequestError(messages.MCP_DRAFT_ONLY)
            if name == "create_requirement_document":
                return summary(
                    RequirementDocumentService().create_document(
                        db,
                        project_id=project_id,
                        document_in=data.data,
                        actor_id=actor_id,
                    )
                )
            if name == "create_requirement_section":
                return summary(
                    RequirementSectionService().create_section(
                        db,
                        project_id=project_id,
                        document_id=data.document_id,
                        section_in=data.data,
                        actor_id=actor_id,
                    )
                )
            return summary(
                RequirementService().create_requirement(
                    db,
                    project_id=project_id,
                    requirement_in=data.data,
                    actor_id=actor_id,
                )
            )
        if name == "comment_requirement":
            row = self.target(
                db, project_id, data.target_type, data.target_id, lock=True
            )
            self.version(row, data.version)
            fields = {
                "document": {
                    "title",
                    "purpose",
                    "target_system_name",
                    "client_name",
                    "vendor_name",
                },
                "section": {"title", "content"},
                "requirement_item": {
                    "title",
                    "description",
                    "rationale",
                    "acceptance_criteria",
                    "category",
                    "source",
                },
                "open_issue": {"title", "description", "resolution"},
            }
            if data.field not in fields[data.target_type]:
                raise BadRequestError(messages.MCP_INVALID_ANCHOR)
            value = getattr(row, data.field, None) or ""
            if (
                value[data.quote_start : data.quote_start + len(data.quote)]
                != data.quote
            ):
                raise ConflictError(messages.MCP_STALE_QUOTE)
            anchor = {
                "kind": "requirement_field"
                if data.target_type == "requirement_item"
                else "field",
                "field": data.field,
                "label": data.field,
                "quote": data.quote,
                "quote_start": data.quote_start,
                "offset_unit": "unicode_code_point",
                "target_version": data.version,
                "scope": "section_requirements_review"
                if data.target_type == "requirement_item"
                else "document_preview"
                if data.target_type == "document"
                else "section_review",
                "source_view": "requirement_document_overview_tab",
                "document_id": row.id
                if data.target_type == "document"
                else row.document_id,
                "requirement_id": row.id
                if data.target_type == "requirement_item"
                else None,
                "section_id": row.id
                if data.target_type == "section"
                else getattr(row, "section_id", None),
                "preview_target_type": "document"
                if data.target_type == "document"
                else "section",
                "preview_target_id": row.id
                if data.target_type in {"document", "section"}
                else getattr(row, "section_id", None),
            }
            comment = RequirementTargetCommentService().create_comment(
                db,
                project_id=project_id,
                comment_in=RequirementTargetCommentCreate(
                    target_type=data.target_type,
                    target_id=data.target_id,
                    target_anchor=anchor,
                    body=data.body,
                ),
                author_id=actor_id,
            )
            return {
                "id": comment.id,
                "target_anchor": anchor,
                "target_type": data.target_type,
                "target_id": data.target_id,
            }
        if name == "create_test_design":
            return summary(self.designs.create(db, project_id, data.data, actor_id))
        if name == "append_test_design":
            design = self.designs.get(db, project_id, data.design_id, lock=True)
            self.version(design, data.version)
            graph = self.designs.read(db, design).model_dump(
                mode="json", include=set(TestDesignUpdate.model_fields)
            )
            additions = data.model_dump(
                mode="json",
                exclude={"project_id", "idempotency_key", "design_id", "version"},
            )
            for key, rows in additions.items():
                if self.repository.design_ids_exist(
                    db, key, [row["id"] for row in rows]
                ):
                    raise ConflictError(messages.MCP_EXISTING_ID)
                if {row["id"] for row in rows} & {row["id"] for row in graph[key]}:
                    raise ConflictError(messages.MCP_EXISTING_ID)
                position = (
                    max((row.get("position", 0) for row in graph[key]), default=-1) + 1
                )
                graph[key] += [
                    {**row, "position": position + index}
                    for index, row in enumerate(rows)
                ]
            try:
                updated = TestDesignUpdate.model_validate(graph)
            except ValidationError as exc:
                raise BadRequestError(messages.MCP_INVALID_INPUT) from exc
            return summary(
                self.designs.update(db, project_id, design.id, updated, actor_id)
            )
        if name == "comment_test_design":
            design = self.designs.get(db, project_id, data.design_id, lock=True)
            self.version(design, data.version)
            if not data.data.field or (
                data.data.target_type == "combination"
                and data.data.field.startswith("level:")
                and len(data.data.field.split(":")) != 3
            ):
                raise BadRequestError(messages.MCP_INVALID_ANCHOR)
            return (
                TestCollaborationService()
                .create_comment(db, project_id, design.id, data.data, actor_id)
                .model_dump(mode="json")
            )
        if name == "link_requirement_test_item":
            return (
                TestCollaborationService()
                .create_link(db, project_id, data.design_id, data.data, actor_id)
                .model_dump(mode="json")
            )
        if name == "create_task":
            if (
                data.data.requirement_id is not None
                and not self.auth.authorization.has_project_permission(
                    db,
                    user=user,
                    project_id=project_id,
                    permission_code="requirement:read",
                )
            ):
                raise ForbiddenError()
            return summary(
                self.tasks.create_task(
                    db, project_id=project_id, task_in=data.data, actor_id=actor_id
                )
            )
        if name == "update_task":
            self.task(db, project_id, data.task_id)
            return summary(
                self.tasks.update_task(
                    db, task_id=data.task_id, task_in=data.data, actor_id=actor_id
                )
            )
        if name == "create_task_dependency":
            self.task(db, project_id, data.data.predecessor_task_id)
            self.task(db, project_id, data.data.successor_task_id)
            row = TaskDependencyService().create_dependency(
                db, dependency_in=data.data, actor_id=actor_id
            )
            return {"id": row.id, **data.data.model_dump(mode="json")}
        if name == "create_milestone":
            return summary(
                TaskMilestoneService().create_milestone(
                    db, project_id=project_id, milestone_in=data.data, actor_id=actor_id
                )
            )
        if name == "preview_task_schedule":
            if len({change.task_id for change in data.changes}) != len(data.changes):
                raise BadRequestError(messages.MCP_INVALID_INPUT)
            before = []
            for change in data.changes:
                row = self.task(db, project_id, change.task_id)
                self.version(row, change.version)
                if (
                    change.start_date
                    and change.due_date
                    and change.start_date > change.due_date
                ):
                    raise BadRequestError(messages.MCP_INVALID_INPUT)
                before.append(summary(row))
            result = {
                "kind": "task_schedule_preview",
                "project_id": project_id,
                "before": before,
                "changes": [row.model_dump(mode="json") for row in data.changes],
                "assumptions": data.assumptions,
            }
            return {
                **result,
                "preview_key": data.idempotency_key,
                "confirmation_hash": request_digest(result),
            }
        if name == "apply_task_schedule":
            receipt = self.repository.receipt(db, connection.id, data.preview_key)
            if receipt is None or receipt.created_at < datetime.now(UTC) - timedelta(
                minutes=15
            ):
                raise ConflictError(messages.MCP_PREVIEW_EXPIRED)
            plan = receipt.result
            if (
                plan.get("kind") != "task_schedule_preview"
                or plan.get("project_id") != project_id
                or not hmac.compare_digest(
                    plan.get("confirmation_hash", ""), data.confirmation_hash
                )
            ):
                raise BadRequestError(messages.MCP_INVALID_INPUT)
            changes = [s.ScheduleChange.model_validate(row) for row in plan["changes"]]
            # 全件を先にロック・検証し、途中失敗は外側のtransactionで戻す。
            for change in sorted(changes, key=lambda row: row.task_id):
                row = self.repository.task_for_update(db, change.task_id)
                if row is None or row.project_id != project_id:
                    raise NotFoundError()
                self.version(row, change.version)
            return {
                "items": [
                    summary(
                        self.tasks.update_task(
                            db,
                            task_id=change.task_id,
                            task_in=TaskUpdate(
                                version=change.version,
                                start_date=change.start_date,
                                due_date=change.due_date,
                            ),
                            actor_id=actor_id,
                        )
                    )
                    for change in changes
                ]
            }
        raise NotFoundError()
