"""新しい証拠操作だけのREAD COMMITTED・短い認可境界。"""

from contextlib import contextmanager

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.base import Base
from app.models.document import DocumentAttachment, ProjectDocument
from app.models.project import Project
from app.models.requirement_verification import RequirementVerificationRevision
from app.models.session import UserSession
from app.models.test_design import (
    RequirementTestItem,
    TestCase,
    TestDesign,
    TestEvidence,
    TestExecution,
    TestItem,
)
from app.models.user import User
from app.services.publication_access_context import PublicationAccessContext
from app.services.requirement_verification import READS


class EvidenceAccess:
    """運営/組織役職の管理bypassや公開権限を利用しない。"""

    def __init__(self):
        self.context = PublicationAccessContext()

    @staticmethod
    def enabled():
        return (
            settings.requirement_verification_preparation_enabled
            and settings.requirement_verification_evidence_enabled
        )

    def can(self, db, actor, project_id, code):
        return self.context.authorization.has_project_permission(
            db, user=actor, project_id=project_id, permission_code=code
        )

    def require(self, db, actor, project_id, *, submit=False, document=False):
        if not self.enabled():
            raise NotFoundError()
        for code in (
            READS
            + (("requirement_verification:submit",) if submit else ())
            + (("document:read",) if document else ())
        ):
            self.context.permission(db, actor, project_id, code)

    def lock_permissions(self, db, project_id, user_id):
        tables = Base.metadata.tables
        members, roles, rp, permissions = (
            tables[n]
            for n in ("project_members", "roles", "role_permissions", "permissions")
        )
        ids = list(
            db.scalars(
                select(members.c.role_id)
                .where(
                    members.c.project_id == project_id,
                    members.c.user_id == user_id,
                    members.c.deleted_at.is_(None),
                )
                .order_by(members.c.id)
                .with_for_update(read=True)
            )
        )
        list(
            db.execute(
                select(roles.c.id)
                .where(roles.c.id.in_(ids))
                .order_by(roles.c.id)
                .with_for_update(read=True)
            )
        )
        list(
            db.execute(
                select(permissions.c.id)
                .where(
                    permissions.c.id.in_(
                        select(rp.c.permission_id).where(rp.c.role_id.in_(ids))
                    )
                )
                .order_by(permissions.c.id)
                .with_for_update(read=True)
            )
        )
        list(
            db.execute(
                select(rp)
                .where(rp.c.role_id.in_(ids))
                .order_by(rp.c.role_id, rp.c.permission_id)
                .with_for_update(read=True)
            )
        )

    def lock_sources(self, db, project_id, selection, plan_id):
        """回収処理の親行→Project境界の順。Storage IO中は保持しない。"""
        if selection is None:
            return
        executions = [e.execution_id for e in selection.entries if e.execution_id]
        revision = db.scalar(
            select(RequirementVerificationRevision.checklist).where(
                RequirementVerificationRevision.plan_id == plan_id,
                RequirementVerificationRevision.version == selection.plan_version,
            )
        )
        selected_rows = {str(e.row_id) for e in selection.entries}
        item_ids = [
            t["item_id"]
            for r in (revision or {}).get("rows", [])
            if r["id"] in selected_rows
            for t in r.get("targets", [])
        ]
        attachments = [
            f.id
            for e in selection.entries
            for f in e.files
            if f.kind == "document_attachment"
        ]
        evidence = [
            f.id
            for e in selection.entries
            for f in e.files
            if f.kind == "test_evidence"
        ]
        designs = (
            select(TestCase.design_id)
            .join(TestExecution, TestExecution.case_id == TestCase.id)
            .where(TestExecution.id.in_(executions))
        )
        designs = designs.union(
            select(TestItem.design_id).where(TestItem.id.in_(item_ids))
        )
        list(
            db.execute(
                select(TestDesign.id)
                .where(TestDesign.project_id == project_id, TestDesign.id.in_(designs))
                .order_by(TestDesign.id)
                .with_for_update(read=True)
            )
        )
        docs = select(DocumentAttachment.document_id).where(
            DocumentAttachment.id.in_(attachments)
        )
        list(
            db.execute(
                select(ProjectDocument.id)
                .where(
                    ProjectDocument.project_id == project_id,
                    ProjectDocument.id.in_(docs),
                )
                .order_by(ProjectDocument.id)
                .with_for_update(read=True)
            )
        )
        for model, identities in (
            (TestExecution, executions),
            (TestEvidence, evidence),
            (DocumentAttachment, attachments),
        ):
            list(
                db.execute(
                    select(model.id)
                    .where(model.id.in_(identities))
                    .order_by(model.id)
                    .with_for_update(read=True)
                )
            )
        list(
            db.execute(
                select(TestCase.id)
                .where(
                    TestCase.id.in_(
                        select(TestExecution.case_id).where(
                            TestExecution.id.in_(executions)
                        )
                    )
                )
                .order_by(TestCase.id)
                .with_for_update(read=True)
            )
        )
        list(
            db.execute(
                select(RequirementTestItem.id)
                .where(RequirementTestItem.item_id.in_(item_ids))
                .order_by(RequirementTestItem.id)
                .with_for_update(read=True)
            )
        )

    @contextmanager
    def transaction(
        self, original, user, project_id, *, write=False, selection=None, plan_id=None
    ):
        if (
            settings.is_public_environment
            or original.info.get("demo_id") is not None
            or original.info.get("data_realm") == "demo"
            or original.info.get("mcp_connection_id") is not None
            or original.info.get("tenant_id") is None
            or original.info.get("tenant_user_id") != user.id
        ):
            raise ForbiddenError()
        info = {
            k: original.info[k]
            for k in ("tenant_id", "tenant_user_id", "data_realm")
            if k in original.info
        }
        with (
            original.get_bind()
            .engine.connect()
            .execution_options(isolation_level="READ COMMITTED") as connection,
            Session(bind=connection, info=info) as db,
            db.begin(),
        ):
            db.execute(text("SET LOCAL lock_timeout='1s'"))
            if write:
                db.execute(
                    select(User.id)
                    .where(User.id == user.id)
                    .with_for_update(read=True, key_share=True)
                )
                db.execute(
                    select(Project.id)
                    .where(Project.id == project_id)
                    .with_for_update(read=True, key_share=True)
                )
                self.lock_sources(db, project_id, selection, plan_id)
            db.execute(text("SELECT pg_advisory_xact_lock_shared(1398362932,0)"))
            db.execute(
                text("SELECT publication_lock(:project,true)"), {"project": project_id}
            )
            if write:
                active = original.info.get("auth_session")
                if active:
                    db.execute(
                        select(UserSession.id)
                        .where(UserSession.id == active.id)
                        .with_for_update(read=True)
                    )
                self.lock_permissions(db, project_id, user.id)
            actor = self.context.verify(db, original, user, internal=True)
            self.context.permission(db, actor, project_id, "project:read")
            yield db, actor
