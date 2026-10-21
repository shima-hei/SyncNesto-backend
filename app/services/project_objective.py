"""案件目標の出典を業務認可し、選択と履歴を原子的に保存する。"""

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.config import settings
from app.core.exceptions import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    VersionConflictError,
)
from app.models.project_goal_source_event import ProjectGoalSourceEvent
from app.models.user import User, UserType
from app.repositories.project_objective import ProjectObjectiveRepository
from app.schemas.project_objective import (
    GoalSourceEventPage,
    GoalSourceEventRead,
    ObjectiveDocument,
    ProjectObjectiveRead,
    ProjectObjectiveUpdate,
)
from app.services.audit_log import AuditLogService
from app.services.authorization import AuthorizationService


class ProjectObjectiveService:
    """参照の選択と文書本文の編集権限を分ける。"""

    def __init__(self):
        """既存の業務認可とRepositoryを利用する。"""
        self.repository = ProjectObjectiveRepository()
        self.authorization = AuthorizationService()

    def can(self, db, user, project_id, code):
        """既存のtenant/案件所属を含むpermissionを判定する。"""
        return self.authorization.has_project_permission(
            db, user=user, project_id=project_id, permission_code=code
        )

    def access(self, db, user, project_id):
        """管理メタデータ権限やMCP経路で業務認可を迂回しない。"""
        if (
            user.user_type != UserType.INTERNAL.value
            or db.info.get("mcp_connection_id")
            or db.info.get("demo_id")
            or db.info.get("data_realm") == "demo"
            or not self.can(db, user, project_id, "project:read")
        ):
            raise ForbiddenError()
        project = self.repository.project(db, project_id)
        if project is None:
            raise NotFoundError()
        return project

    def read(self, db: Session, user: User, project_id: int) -> ProjectObjectiveRead:
        """未登録・未選択・権限外を件数0へ変換しない。"""
        if db.info.get("demo_id") or db.info.get("data_realm") == "demo":
            return ProjectObjectiveRead(enabled=False, state="disabled")
        project = self.access(db, user, project_id)
        if not settings.project_objectives_enabled:
            return ProjectObjectiveRead(enabled=False, state="disabled")
        if not self.can(db, user, project_id, "requirement:read"):
            return ProjectObjectiveRead(enabled=True, state="restricted")
        document = (
            self.repository.document(db, project_id, project.goal_document_id)
            if project.goal_document_id is not None
            else None
        )
        return ProjectObjectiveRead(
            enabled=True,
            state="available"
            if document
            else "unselected"
            if project.goal_document_id is None
            else "unavailable",
            version=project.goal_source_version,
            document=ObjectiveDocument.model_validate(document) if document else None,
            can_manage_source=self.can(
                db, user, project_id, "project:manage_goal_source"
            ),
            can_edit_document=self.can(db, user, project_id, "requirement:update"),
            can_create_document=self.can(db, user, project_id, "requirement:create"),
        )

    def update(
        self, db: Session, user: User, project_id: int, data: ProjectObjectiveUpdate
    ) -> ProjectObjectiveRead:
        """版確認した出典と追記履歴を同じtransactionで確定する。"""
        try:
            project = self.access(db, user, project_id)
            if not settings.project_objectives_enabled or not all(
                self.can(db, user, project_id, code)
                for code in ("requirement:read", "project:manage_goal_source")
            ):
                raise ForbiddenError()
            if project.goal_source_version != data.version:
                raise VersionConflictError({"version": project.goal_source_version})
            document = (
                self.repository.document(db, project_id, data.document_id, lock=True)
                if data.document_id is not None
                else None
            )
            if data.document_id is not None and document is None:
                raise NotFoundError()
            if document and document.version != data.expected_document_version:
                raise VersionConflictError({"document_version": document.version})
            if project.goal_document_id == data.document_id:
                return self.read(db, user, project_id)
            reason = (data.reason or "").strip() or None
            if project.goal_document_id is not None and reason is None:
                raise BadRequestError(error_messages.OBJECTIVE_REASON_REQUIRED)
            previous = project.goal_document_id
            if not self.repository.select_source(db, project, data.document_id):
                raise VersionConflictError({})
            db.add(
                ProjectGoalSourceEvent(
                    project_id=project_id,
                    previous_document_id=previous,
                    document_id=data.document_id,
                    previous_version=data.version,
                    version=data.version + 1,
                    document_version=document.version if document else None,
                    reason=reason,
                    changed_by=user.id,
                )
            )
            AuditLogService().record(
                db,
                event_type="project.goal_source_changed",
                tenant_id=project.tenant_id,
                actor_user_id=user.id,
                project_id=project_id,
                resource_type="project",
                resource_id=project_id,
                metadata={
                    "previous_document_id": previous,
                    "document_id": data.document_id,
                    "version": data.version + 1,
                },
                commit=False,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return self.read(db, user, project_id)

    def events(
        self, db: Session, user: User, project_id: int, page: int, page_size: int
    ) -> GoalSourceEventPage:
        """閲覧可能な同案件の選択履歴だけを返す。"""
        self.access(db, user, project_id)
        if not self.can(db, user, project_id, "requirement:read"):
            raise ForbiddenError()
        rows, total, names, titles = self.repository.events(
            db, project_id, page, page_size
        )
        return GoalSourceEventPage(
            items=[
                GoalSourceEventRead(
                    **{
                        key: getattr(row, key)
                        for key in GoalSourceEventRead.model_fields
                        if key
                        not in {
                            "changed_by_name",
                            "previous_document_title",
                            "document_title",
                        }
                    },
                    changed_by_name=names.get(row.changed_by),
                    previous_document_title=titles.get(row.previous_document_id or 0),
                    document_title=titles.get(row.document_id or 0),
                )
                for row in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
        )
