"""既存データを取り込まず、審査専用の組織とサンプルだけを発行する。"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models.mcp import McpConnection
from app.models.project import Project, ProjectMember
from app.models.rbac import Role
from app.models.requirement import Requirement, RequirementDocument
from app.models.review_account import ReviewAccountGrant
from app.models.session import UserSession
from app.models.tenant import Tenant, TenantMember
from app.models.test_design import (
    ExpectedValue,
    Factor,
    FactorLevel,
    PatternTable,
    TestDesign,
    TestPattern,
    TestPatternExpectedValue,
    TestPatternValue,
)
from app.models.user import User
from app.services.audit_log import AuditLogService


class ReviewFixtureService:
    """管理APIを公開せず、運用CLIのtransaction内で審査fixtureを作る。"""

    def create(self, db: Session, password: str, expires_at: datetime) -> dict:
        """通常ユーザーの転用を禁止し、毎回新しい専用Identityを発行する。"""
        if db.info.get("data_realm") == "demo" or db.info.get("tenant_id"):
            raise ValueError("通常DBの運用セッションが必要です")
        if expires_at <= datetime.now(UTC):
            raise ValueError("将来の有効期限が必要です")
        suffix = uuid4().hex[:12]
        user = User(
            email=f"review-{suffix}@review.syncnesto.example.com",
            name="Plugin reviewer",
            user_type="guest",
            hashed_password=get_password_hash(password),
            avatar_key=None,
        )
        tenant = Tenant(name="Plugin review samples", slug=f"mcp-review-{suffix}")
        db.add_all([user, tenant])
        db.flush()
        tenant_role = (
            db.query(Role).filter_by(key="tenant_member", scope="tenant").one()
        )
        manager = db.query(Role).filter_by(key="manager", scope="project").one()
        viewer = db.query(Role).filter_by(key="viewer", scope="project").one()
        db.add_all(
            [
                TenantMember(
                    tenant_id=tenant.id, user_id=user.id, role_id=tenant_role.id
                ),
                ReviewAccountGrant(
                    user_id=user.id, tenant_id=tenant.id, expires_at=expires_at
                ),
            ]
        )
        projects = []
        for index, label in enumerate(("consented", "not consented", "read only")):
            project = Project(
                tenant_id=tenant.id,
                project_code=f"REVIEW-{index + 1}",
                name=f"Plugin review - {label}",
                created_by=user.id,
            )
            db.add(project)
            db.flush()
            db.add(
                ProjectMember(
                    project_id=project.id,
                    user_id=user.id,
                    role_id=(viewer if index == 2 else manager).id,
                )
            )
            projects.append(project)
        db.flush()
        document = RequirementDocument(
            project_id=projects[1].id,
            document_code="REVIEW-N1",
            title="Unconsented sample requirements",
            created_by=user.id,
        )
        db.add(document)
        db.flush()
        requirement = Requirement(
            document_id=document.id,
            requirement_code="SAMPLE-N1",
            requirement_type="functional",
            title="Unconsented sample requirement",
            description=(
                "This sample must not be returned by the consented MCP connection."
            ),
            created_by=user.id,
        )
        db.add(requirement)
        design = TestDesign(
            project_id=projects[0].id,
            name="Plugin review - cell fixture",
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(design)
        db.flush()
        table = PatternTable(design_id=design.id, name="Authentication")
        db.add(table)
        db.flush()
        factor = Factor(design_id=design.id, table_id=table.id, name="Password")
        expected = ExpectedValue(
            design_id=design.id, table_id=table.id, name="Sign-in rejected"
        )
        pattern = TestPattern(
            design_id=design.id,
            table_id=table.id,
            code="Invalid password",
        )
        db.add_all([factor, expected, pattern])
        db.flush()
        valid = FactorLevel(design_id=design.id, factor_id=factor.id, name="valid")
        invalid = FactorLevel(
            design_id=design.id, factor_id=factor.id, name="invalid", position=1
        )
        db.add_all([valid, invalid])
        db.flush()
        db.add_all(
            [
                TestPatternValue(
                    design_id=design.id,
                    pattern_id=pattern.id,
                    factor_id=factor.id,
                    level_id=valid.id,
                ),
                TestPatternExpectedValue(
                    design_id=design.id,
                    pattern_id=pattern.id,
                    expected_value_id=expected.id,
                ),
            ]
        )
        db.flush()
        AuditLogService().record(
            db,
            event_type="review.account_created",
            tenant_id=tenant.id,
            target_user_id=user.id,
            resource_type="user",
            resource_id=user.id,
            commit=False,
        )
        return {
            "user_id": user.id,
            "email": user.email,
            "tenant_id": tenant.id,
            "expires_at": expires_at.isoformat(),
            "consented_project_id": projects[0].id,
            "unconsented_project_id": projects[1].id,
            "read_only_project_id": projects[2].id,
            "unconsented_requirement_id": requirement.id,
            "cell_fixture": {
                "design_id": design.id,
                "version": design.version,
                "table_id": str(table.id),
                "pattern_id": str(pattern.id),
                "factor_id": str(factor.id),
                "level_id": str(valid.id),
                "correct_level_id": str(invalid.id),
            },
        }

    def revoke(self, db: Session, user_id: int) -> None:
        """審査許可・Webセッション・MCP委任を同時に失効する。データは保持する。"""
        grant = (
            db.query(ReviewAccountGrant)
            .filter_by(user_id=user_id)
            .with_for_update()
            .one()
        )
        now = datetime.now(UTC)
        grant.revoked_at = now
        db.query(UserSession).filter_by(user_id=user_id, revoked_at=None).update(
            {
                UserSession.revoked_at: now,
                UserSession.revoked_reason: "review_access_revoked",
            },
            synchronize_session=False,
        )
        db.query(McpConnection).filter_by(user_id=user_id, revoked_at=None).update(
            {
                McpConnection.revoked_at: now,
            },
            synchronize_session=False,
        )
        AuditLogService().record(
            db,
            event_type="review.account_revoked",
            tenant_id=grant.tenant_id,
            target_user_id=user_id,
            resource_type="user",
            resource_id=user_id,
            commit=False,
        )
