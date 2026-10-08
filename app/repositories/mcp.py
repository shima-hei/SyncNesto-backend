"""MCP認可・資格情報の保存と排他制御。"""

import hashlib
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.models.mcp import (
    McpAuthorizationRequest,
    McpConnection,
    McpCredential,
    McpOperationReceipt,
)
from app.models.project import Project, ProjectMember
from app.models.requirement import (
    Requirement,
    RequirementDocument,
    RequirementOpenIssue,
    RequirementSection,
)
from app.models.task import Task
from app.models.tenant import Tenant, TenantMember
from app.models.user import User
from app.repositories.test_design import ENTITY_MODELS


class McpRepository:
    """commitは認可Serviceが制御する。"""

    def lock_operation(self, db: Session, connection_id: UUID, key: str) -> None:
        """読み取りsavepointを閉じ、ロックを外側で取得してrollbackでも保持する。"""
        outer = db.info.get("mcp_outer_connection")
        if not isinstance(outer, Connection) or not outer.in_transaction():
            raise RuntimeError("MCP operation requires an outer transaction")
        db.commit()
        raw = hashlib.sha256(f"{connection_id}:{key}".encode()).digest()[:8]
        outer.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": int.from_bytes(raw, "big", signed=True)},
        )

    def consent_projects(self, db: Session, user_id: int):
        """有効な組織所属内の候補を返し、Serviceが業務権限を絞り込む。"""
        return db.execute(
            select(Project, Tenant)
            .join(Tenant, Tenant.id == Project.tenant_id)
            .join(TenantMember, TenantMember.tenant_id == Tenant.id)
            .where(
                TenantMember.user_id == user_id,
                TenantMember.status == "active",
                Tenant.status == "active",
                Project.deleted_at.is_(None),
            )
            .order_by(Tenant.id, Project.id)
        ).all()

    def user(self, db: Session, user_id: int) -> User | None:
        """資格情報から解決した本人を取得する。"""
        return db.get(User, user_id)

    def membership(self, db: Session, user_id: int, tenant_id: int):
        """現在有効な組織所属だけを返す。"""
        return db.scalar(
            select(TenantMember)
            .join(Tenant)
            .where(
                TenantMember.user_id == user_id,
                TenantMember.tenant_id == tenant_id,
                TenantMember.status == "active",
                Tenant.status == "active",
            )
        )

    def receipt(self, db: Session, connection_id: UUID, key: str):
        """本人の接続内の再送結果・確認用プレビューだけを取得する。"""
        return db.scalar(
            select(McpOperationReceipt).where(
                McpOperationReceipt.connection_id == connection_id,
                McpOperationReceipt.operation_key == key,
            )
        )

    def save_receipt(
        self,
        db: Session,
        connection_id: UUID,
        key: str,
        request_hash: str,
        result: dict,
    ) -> None:
        """業務変更と同じ外側のtransactionで保存する。"""
        db.add(
            McpOperationReceipt(
                connection_id=connection_id,
                operation_key=key,
                request_hash=request_hash,
                result=result,
            )
        )
        db.flush()

    def target(
        self,
        db: Session,
        project_id: int,
        target_type: str,
        target_id: int,
        *,
        lock: bool = False,
    ):
        """要件定義対象をProject境界で解決する。"""
        models = {
            "document": RequirementDocument,
            "section": RequirementSection,
            "requirement_item": Requirement,
            "open_issue": RequirementOpenIssue,
        }
        model = models[target_type]
        query = select(model).where(model.id == target_id, model.deleted_at.is_(None))
        if model is RequirementDocument:
            query = query.where(RequirementDocument.project_id == project_id)
        else:
            query = query.join(
                RequirementDocument, model.document_id == RequirementDocument.id
            ).where(
                RequirementDocument.project_id == project_id,
                RequirementDocument.deleted_at.is_(None),
            )
        return db.scalar(query.with_for_update(of=model) if lock else query)

    def project(self, db: Session, project_id: int):
        """認可済みProjectの情報を取得する。"""
        return db.get(Project, project_id)

    def task_for_update(self, db: Session, task_id: int):
        """日程の一括変更対象をロックする。"""
        return db.scalar(
            select(Task)
            .where(Task.id == task_id, Task.deleted_at.is_(None))
            .with_for_update()
        )

    def design_ids_exist(self, db: Session, component: str, ids: list[str]) -> bool:
        """削除済みのIDも追加で復活させない。"""
        if not ids:
            return False
        model = ENTITY_MODELS[component]
        return (
            db.scalar(
                select(model.id)
                .where(model.id.in_([UUID(value) for value in ids]))
                .limit(1)
            )
            is not None
        )

    def members(self, db: Session, project_id: int):
        """担当者候補。メール・認証情報は含めない。"""
        return db.execute(
            select(User.id, User.name)
            .join(ProjectMember, ProjectMember.user_id == User.id)
            .where(
                ProjectMember.project_id == project_id,
                ProjectMember.deleted_at.is_(None),
                User.is_active.is_(True),
            )
            .limit(100)
        ).all()

    def request(self, db: Session, request_id: UUID, *, lock: bool = False):
        """認可要求を取得する。"""
        query = select(McpAuthorizationRequest).where(
            McpAuthorizationRequest.id == request_id
        )
        return db.scalar(query.with_for_update() if lock else query)

    def code(self, db: Session, digest: str):
        """認可コードをロックして取得する。"""
        return db.scalar(
            select(McpAuthorizationRequest)
            .where(McpAuthorizationRequest.code_hash == digest)
            .with_for_update()
        )

    def credential(self, db: Session, digest: str):
        """資格情報を取得する。ローテーションは接続のロックで直列化する。"""
        return db.scalar(
            select(McpCredential).where(McpCredential.token_hash == digest)
        )

    def connection(self, db: Session, connection_id: UUID, *, lock: bool = False):
        """接続を取得する。"""
        query = select(McpConnection).where(McpConnection.id == connection_id)
        return db.scalar(query.with_for_update() if lock else query)

    def list_connections(self, db: Session, user_id: int):
        """本人の接続だけを新しい順に返す。"""
        return db.scalars(
            select(McpConnection)
            .where(McpConnection.user_id == user_id)
            .order_by(McpConnection.created_at.desc())
            .limit(100)
        ).all()
