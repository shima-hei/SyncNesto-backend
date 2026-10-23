"""固定承認の読取とflushのみを担当し、transactionを確定しない。"""

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.models.artifact_release import ArtifactRelease, ArtifactReleaseNode
from app.models.fixed_approval import (
    FixedApprovalEvent,
    FixedApprovalRequest,
    FixedApprovalRequirementBasis,
    LegacyApprovalImport,
)
from app.models.project import ProjectMember
from app.models.rbac import Permission, Role, RolePermission
from app.models.requirement import (
    RequirementApproval,
    RequirementChangeLog,
    RequirementDocument,
)
from app.models.review_account import ReviewAccountGrant
from app.models.tenant import TenantMember
from app.models.user import User


class FixedApprovalRepository:
    """Projectと固定版に結び付けた証跡の取得。"""

    def get(self, db: Session, project_id, approval_id, *, lock=False):
        """別Projectや保存途中の依頼を返さない。"""
        query = select(FixedApprovalRequest).where(
            FixedApprovalRequest.project_id == project_id,
            FixedApprovalRequest.id == approval_id,
            FixedApprovalRequest.sealed.is_(True),
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return db.scalar(query)

    def by_operation(self, db, project_id, actor_id, request_id):
        """同じ申請操作の再送を取得する。"""
        return db.scalar(
            select(FixedApprovalRequest).where(
                FixedApprovalRequest.project_id == project_id,
                FixedApprovalRequest.requester_id == actor_id,
                FixedApprovalRequest.request_id == request_id,
                FixedApprovalRequest.sealed.is_(True),
            )
        )

    def nodes(self, db, release_id):
        """元データではなく封印済みnodeを読む。"""
        return list(
            db.scalars(
                select(ArtifactReleaseNode)
                .where(
                    ArtifactReleaseNode.release_id == release_id,
                )
                .order_by(ArtifactReleaseNode.ordinal)
            )
        )

    def events(self, db, approval_id):
        """申請と終判断を順序付きで読む。"""
        return list(
            db.scalars(
                select(FixedApprovalEvent)
                .where(
                    FixedApprovalEvent.approval_id == approval_id,
                )
                .order_by(FixedApprovalEvent.seq)
            )
        )

    def bases(self, db, approval_id):
        """文書の固定した合意・例外根拠を読む。"""
        return list(
            db.scalars(
                select(FixedApprovalRequirementBasis).where(
                    FixedApprovalRequirementBasis.approval_id == approval_id,
                )
            )
        )

    def visible(self, project_id, allowed_kinds, *, document_read):
        """一覧と件数、合意候補に共通の公開範囲。"""
        query = (
            select(FixedApprovalRequest)
            .join(
                ArtifactRelease,
                ArtifactRelease.id == FixedApprovalRequest.release_id,
            )
            .where(
                FixedApprovalRequest.project_id == project_id,
                FixedApprovalRequest.sealed.is_(True),
                ArtifactRelease.source_kinds.contained_by(allowed_kinds),
            )
        )
        if not document_read:
            query = query.where(FixedApprovalRequest.preservation_id.is_(None))
        return query

    def candidates(self, db, project_id, actor_id, codes, q=None):
        """同Projectの適格な本人を、権限の積集合で絞る。"""
        query = (
            select(User)
            .join(ProjectMember, ProjectMember.user_id == User.id)
            .join(
                TenantMember,
                TenantMember.user_id == User.id,
            )
            .join(Role, Role.id == ProjectMember.role_id)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .join(
                Permission,
                Permission.id == RolePermission.permission_id,
            )
            .where(
                ProjectMember.project_id == project_id,
                ProjectMember.deleted_at.is_(None),
                TenantMember.tenant_id == db.info["tenant_id"],
                TenantMember.status == "active",
                User.id != actor_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
                User.user_type == "internal",
                Role.scope == "project",
                User.id.not_in(select(ReviewAccountGrant.user_id)),
                User.password_change_required.is_(False),
                Permission.code.in_(codes),
            )
            .group_by(User.id)
            .having(func.count(distinct(Permission.code)) == len(set(codes)))
        )
        if q:
            query = query.where(User.name.ilike(f"%{q}%"))
        return query

    def legacy(self, db, project_id, identity, *, lock=False):
        """元の承認IDと所有Projectを確認する。"""
        query = self.legacy_visible(project_id).where(
            RequirementApproval.id == identity
        )
        if lock:
            query = query.with_for_update(of=RequirementApproval).execution_options(
                populate_existing=True
            )
        return db.scalar(query)

    def legacy_visible(self, project_id):
        """現在残る旧履歴のProject条件。"""
        return (
            select(RequirementApproval)
            .join(RequirementDocument)
            .where(
                RequirementDocument.project_id == project_id,
            )
        )

    def legacy_logs(self, db, source):
        """旧snapshotの承認IDで対応させ、対象不一致も証拠に残す。"""
        return list(
            db.scalars(
                select(RequirementChangeLog)
                .where(
                    RequirementChangeLog.document_id == source.document_id,
                    RequirementChangeLog.action.in_(
                        ["approval.requested", "approval.approved", "approval.rejected"]
                    ),
                    RequirementChangeLog.new_value["id"].as_string() == str(source.id),
                )
                .order_by(RequirementChangeLog.id)
            )
        )

    def imported(self, db, project_id, import_id):
        """元本文が削除済みでも、同Projectの取込済み証拠を読む。"""
        return db.scalar(
            select(LegacyApprovalImport).where(
                LegacyApprovalImport.project_id == project_id,
                LegacyApprovalImport.id == import_id,
            )
        )
