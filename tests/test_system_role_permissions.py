"""システム運営権限の移行と業務権限の独立性を検証する。"""

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import select

from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.tenant import TenantMember
from app.repositories.rbac import RbacRepository
from app.services.authorization import AuthorizationService
from scripts.seed_rbac import SYSTEM_ADMIN_PERMISSIONS, seed_roles_and_permissions


@pytest.mark.parametrize("mode", ["migration", "seed"])
def test_system_role_cleanup_preserves_assignments_and_other_roles(
    mode, db, create_test_user, create_test_project, assign_project_role
):
    """旧付与を除いても、兼任者の組織・Project所属と他Roleを保持する。"""
    user = create_test_user(system_role="system_admin")
    project = create_test_project()
    assign_project_role(user=user, project=project, role_key="manager")
    system = db.scalar(
        select(Role).where(Role.key == "system_admin", Role.scope == "system")
    )
    repository = RbacRepository()
    for permission in db.scalars(select(Permission)):
        repository.add_permission_to_role(db, role=system, permission=permission)
    # 同じkeyでもProject scopeのカスタムRoleを変更しない。
    custom = repository.create_role(
        db, key="system_admin", name="Custom project role", scope="project"
    )
    task_read = repository.get_permission_by_code(db, "task:read")
    assert task_read is not None
    repository.add_permission_to_role(db, role=custom, permission=task_read)
    db.commit()
    others = set(
        db.execute(
            select(RolePermission.role_id, RolePermission.permission_id).where(
                RolePermission.role_id != system.id
            )
        )
    )
    users = set(db.execute(select(UserRole.user_id, UserRole.role_id)))
    tenants = set(db.execute(select(TenantMember.user_id, TenantMember.role_id)))

    for _ in range(2):
        if mode == "migration":
            revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision(
                "b7d30e2c941a"
            )
            assert revision is not None
            with Operations.context(MigrationContext.configure(db.connection())):
                revision.module.upgrade()
            db.commit()
        else:
            seed_roles_and_permissions(repository)
        db.expire_all()
        codes = set(
            db.scalars(
                select(Permission.code)
                .join(RolePermission)
                .where(RolePermission.role_id == system.id)
            )
        )
        assert codes == set(SYSTEM_ADMIN_PERMISSIONS)
        assert set(
            db.execute(
                select(RolePermission.role_id, RolePermission.permission_id).where(
                    RolePermission.role_id != system.id
                )
            )
        ) == others
        assert set(db.execute(select(UserRole.user_id, UserRole.role_id))) == users
        assert set(
            db.execute(select(TenantMember.user_id, TenantMember.role_id))
        ) == tenants
        auth = AuthorizationService()
        db.info["tenant_id"] = project.tenant_id
        assert auth.has_system_permission(
            db, user=user, permission_code="user:mfa_recover"
        )
        assert not auth.has_system_permission(
            db, user=user, permission_code="task:read"
        )
        assert auth.has_project_permission(
            db, user=user, project_id=project.id, permission_code="task:update"
        )


def test_system_role_does_not_grant_project_or_mcp_access(
    client, db, create_test_user, create_test_project
):
    """運営Roleだけでは未参加Projectの業務権限・MCP利用を得られない。"""
    from app.services.mcp_auth import McpAuthService
    from tests.helpers.auth import authorize_as

    user = create_test_user(system_role="system_admin")
    project = create_test_project()
    db.info["tenant_id"] = project.tenant_id
    auth = AuthorizationService()
    assert auth.has_system_permission(db, user=user, permission_code="user:read")
    assert not auth.has_project_permission(
        db, user=user, project_id=project.id, permission_code="task:update"
    )
    assert not McpAuthService().eligible(db, user, project.id)
    authorize_as(client, user)
    assert client.get(f"/projects/{project.id}/tasks").status_code == 403
    assert client.get("/users").status_code == 200
