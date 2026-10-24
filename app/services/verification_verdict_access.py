"""判定専用の資格・元データlock。既存の証拠/正式承認の既定値は変更しない。"""

import json

from sqlalchemy import select, text

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.document import DocumentAttachment, ProjectDocument
from app.models.requirement import Requirement, RequirementDetail
from app.models.requirement_verification import RequirementVerificationPlan as Plan
from app.models.test_design import (
    RequirementTestItem,
    TestCase,
    TestDesign,
    TestEvidence,
    TestItem,
)
from app.models.user import User
from app.services.requirement_verification import READS
from app.services.verification_evidence_access import EvidenceAccess


class VerdictAccess(EvidenceAccess):
    @staticmethod
    def enabled():
        return (
            EvidenceAccess.enabled()
            and settings.requirement_verification_decisions_enabled
        )

    def require(
        self, db, actor, project_id, *, prepare=False, decide=False, document=False
    ):
        if not self.enabled():
            raise NotFoundError()
        for code in (
            READS
            + (("requirement_verification:prepare",) if prepare else ())
            + (("requirement_verification:decide",) if decide else ())
            + (("document:read",) if document else ())
        ):
            self.context.permission(db, actor, project_id, code)

    def qualified(self, db, project_id, *, user_id=None, user_ids=None, document=False):
        """有効な同組織の社内所属と全read/decideを一度に確認する。"""
        codes = (
            list(READS)
            + ["requirement_verification:decide"]
            + (["document:read"] if document else [])
        )
        rows = (
            db.execute(
                text("""
          SELECT u.id,u.name,m.id AS membership_id,m.version AS membership_version,
          m.role_id,
                 tm.id AS tenant_membership_id,tm.version AS tenant_membership_version,
                 array_agg(DISTINCT p.code ORDER BY p.code) AS permissions
          FROM project_members m JOIN projects pr ON pr.id=m.project_id
          JOIN users u ON u.id=m.user_id JOIN tenant_members tm ON tm.user_id=u.id
          AND tm.tenant_id=pr.tenant_id
          JOIN tenants t ON t.id=pr.tenant_id JOIN roles r ON r.id=m.role_id
          JOIN role_permissions rp ON rp.role_id=r.id JOIN permissions p ON
          p.id=rp.permission_id
          WHERE pr.id=:project AND pr.tenant_id=:tenant AND pr.deleted_at IS NULL
          AND t.status='active'
            AND tm.status='active' AND u.user_type='internal' AND u.is_active AND
            u.deleted_at IS NULL
            AND NOT u.password_change_required AND m.deleted_at IS NULL AND
            r.scope='project'
            AND (:user_id IS NULL OR u.id=:user_id) AND NOT EXISTS(SELECT FROM
            review_account_grants rg WHERE rg.user_id=u.id)
            AND (CAST(:user_ids AS int[]) IS NULL
              OR u.id=ANY(CAST(:user_ids AS int[])))
          GROUP BY u.id,u.name,m.id,m.version,m.role_id,tm.id,tm.version
          HAVING CAST(:codes AS jsonb) <@ to_jsonb(array_agg(DISTINCT p.code))
          ORDER BY u.id LIMIT 1001
        """),
                {
                    "project": project_id,
                    "tenant": db.info["tenant_id"],
                    "user_id": user_id,
                    "user_ids": user_ids,
                    "codes": json.dumps(codes),
                },
            )
            .mappings()
            .all()
        )
        if user_id is not None and not rows:
            raise ForbiddenError()
        return [dict(r) for r in rows]

    def lock_sources(self, db, project_id, selection, plan_id):
        if plan_id is None:
            return
        plan = db.scalar(
            select(Plan).where(Plan.id == plan_id, Plan.project_id == project_id)
        )
        if plan is None:
            raise NotFoundError()
        list(
            db.execute(
                select(Requirement.id)
                .where(Requirement.id == plan.source_requirement_id)
                .with_for_update(read=True)
            )
        )
        list(
            db.execute(
                select(RequirementDetail.id)
                .where(RequirementDetail.requirement_id == plan.source_requirement_id)
                .order_by(RequirementDetail.id)
                .with_for_update(read=True)
            )
        )
        # 全対象の親designをlockし、既存の実行保存/設計更新と直列化する。
        items = (
            select(TestItem.id)
            .join(RequirementTestItem, RequirementTestItem.item_id == TestItem.id)
            .where(RequirementTestItem.requirement_id == plan.source_requirement_id)
        )
        designs = select(TestItem.design_id).where(TestItem.id.in_(items))
        list(
            db.execute(
                select(TestDesign.id)
                .where(TestDesign.project_id == project_id, TestDesign.id.in_(designs))
                .order_by(TestDesign.id)
                .with_for_update(read=True)
            )
        )
        files = selection.get("files", []) if isinstance(selection, dict) else []
        attachments = [f["id"] for f in files if f["kind"] == "document_attachment"]
        documents = select(DocumentAttachment.document_id).where(
            DocumentAttachment.id.in_(attachments)
        )
        list(
            db.execute(
                select(ProjectDocument.id)
                .where(
                    ProjectDocument.project_id == project_id,
                    ProjectDocument.id.in_(documents),
                )
                .order_by(ProjectDocument.id)
                .with_for_update(read=True)
            )
        )
        for model, identities in (
            (DocumentAttachment, attachments),
            (TestEvidence, [f["id"] for f in files if f["kind"] == "test_evidence"]),
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
                .where(TestCase.design_id.in_(designs))
                .order_by(TestCase.id)
                .with_for_update(read=True)
            )
        )
        list(
            db.execute(
                select(RequirementTestItem.id)
                .where(RequirementTestItem.requirement_id == plan.source_requirement_id)
                .order_by(RequirementTestItem.id)
                .with_for_update(read=True)
            )
        )
        list(
            db.execute(
                select(Plan.id).where(Plan.id == plan_id).with_for_update(read=True)
            )
        )

    def lock_judge(self, db, project_id, user_id):
        db.execute(
            select(User.id)
            .where(User.id == user_id)
            .with_for_update(read=True, key_share=True)
        )
        self.lock_permissions(db, project_id, user_id)
