"""運営用参照の認可・組織境界・兼任・集計を検証する。"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from app.models.rbac import Role, RolePermission
from app.models.tenant import Tenant, TenantMember
from app.models.user import User
from app.services.system_management import SystemManagementService
from tests.helpers.auth import authorize_as


@pytest.fixture
def system_context(client, db, create_test_user):
    """複数組織と停止・削除状態を持つ運営用データを作る。"""
    operator = create_test_user(
        email="operator@example.com", system_role="system_admin"
    )
    shared = create_test_user(email="shared@example.com", name="Shared Identity")
    inactive = create_test_user(email="inactive@example.com")
    removed = create_test_user(email="removed@example.com")
    deleted = create_test_user(email="deleted@example.com")
    inactive.is_active = False
    deleted.deleted_at = datetime.now(UTC)
    a = Tenant(name="Organization A", slug="org-a")
    b = Tenant(name="Organization B", slug="org-b", status="suspended")
    db.add_all([a, b])
    db.flush()
    member_role = db.query(Role).filter_by(key="tenant_member", scope="tenant").one()
    owner_role = db.query(Role).filter_by(key="tenant_owner", scope="tenant").one()
    # 運営者はどの組織にも所属しない状態から確認する。
    db.query(TenantMember).filter_by(user_id=operator.id).delete()
    for user, role, state in [
        (shared, owner_role, "active"),
        (inactive, member_role, "active"),
        (removed, member_role, "removed"),
        (deleted, member_role, "active"),
    ]:
        db.add(
            TenantMember(
                tenant_id=a.id,
                user_id=user.id,
                role_id=role.id,
                display_name=f"A {user.name}",
                department="Engineering",
                status=state,
            )
        )
    db.add(
        TenantMember(
            tenant_id=b.id,
            user_id=shared.id,
            role_id=member_role.id,
            display_name="B Shared",
            status="suspended",
        )
    )
    db.commit()
    authorize_as(client, operator)
    return {
        "operator": operator,
        "shared": shared,
        "inactive": inactive,
        "removed": removed,
        "deleted": deleted,
        "a": a,
        "b": b,
    }


def test_operator_without_membership_reads_all_organizations(
    client, db, system_context
):
    """所属なし・選択組織なしでも停止中を含む全組織を確認できる。"""
    ctx = system_context
    assert client.get("/tenants").json() == []
    client.headers["X-Tenant-ID"] = str(ctx["b"].id)
    response = client.get("/system/tenants", params={"q": "org-a"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == 1
    tenant = result["items"][0]
    assert tenant["id"] == ctx["a"].id
    assert tenant["member_count"] == 2 and tenant["active_member_count"] == 1
    assert tenant["owners"][0]["user_id"] == ctx["shared"].id
    assert client.get(f"/system/tenants/{ctx['b'].id}").json()["status"] == "suspended"
    assert db.query(TenantMember).filter_by(user_id=ctx["operator"].id).count() == 0


def test_members_are_scoped_paginated_and_keep_account_status_separate(
    client, system_context
):
    """組織の所属状態とアカウント停止を混同せず、削除済みを表示しない。"""
    ctx = system_context
    path = f"/system/tenants/{ctx['a'].id}/members"
    result = client.get(path, params={"page_size": 1}).json()
    assert result["total"] == 2 and len(result["items"]) == 1
    second = client.get(path, params={"page_size": 1, "page": 2}).json()["items"][0]
    assert second["user_id"] == ctx["inactive"].id
    assert second["status"] == "active" and second["account_is_active"] is False
    assert client.get(path, params={"q": "shared@example.com"}).json()["total"] == 1
    assert client.get(path, params={"q": "B Shared"}).json()["total"] == 0
    assert client.get(path, params={"status": "suspended"}).json()["total"] == 0
    result = client.get(f"/system/tenants/{ctx['b'].id}/members").json()
    assert result["total"] == 1 and result["items"][0]["status"] == "suspended"


def test_user_detail_lists_multiple_memberships_and_excludes_removed(
    client, system_context
):
    """共通Identityを複製せず、組織別プロフィールを返す。"""
    ctx = system_context
    response = client.get(f"/system/users/{ctx['shared'].id}/memberships")
    rows = {row["tenant_id"]: row for row in response.json()}
    assert rows[ctx["a"].id]["role_key"] == "tenant_owner"
    assert rows[ctx["b"].id]["display_name"] == "B Shared"
    assert rows[ctx["b"].id]["tenant_status"] == "suspended"
    removed = client.get(f"/system/users/{ctx['removed'].id}/memberships").json()
    assert all(row["tenant_id"] != ctx["a"].id for row in removed)
    assert (
        client.get(f"/system/users/{ctx['deleted'].id}/memberships").status_code == 404
    )
    assert client.get(f"/system/users/{ctx['operator'].id}/memberships").json() == []


def test_summary_counts_identities_once_and_excludes_deleted(
    client, db, system_context
):
    """所属数と人数を混同せず、有効な所属なしの運営者も集計する。"""
    response = client.get("/system/summary")
    assert response.status_code == 200, response.text
    summary = response.json()
    users = db.query(User).filter(User.deleted_at.is_(None)).all()
    assert summary["total_users"] == len(users)
    assert summary["active_users"] == sum(user.is_active for user in users)
    assert summary["inactive_users"] == 1
    assert summary["users_without_active_membership"] == 1
    unassigned = client.get("/users", params={"without_active_membership": True}).json()
    assert unassigned["total"] == 1
    assert unassigned["items"][0]["id"] == system_context["operator"].id
    assert summary["suspended_tenants"] == 1
    assert summary["total_tenants"] == summary["active_tenants"] + 1


@pytest.mark.parametrize(
    "path",
    [
        "/system/summary",
        "/system/tenants",
        "/system/tenants/1",
        "/system/tenants/1/members",
        "/system/users/1/memberships",
    ],
)
def test_organization_owner_has_no_operator_access(client, db, create_test_user, path):
    """組織Ownerでも運営用の全組織・Identity参照を許可しない。"""
    user = create_test_user()
    owner = db.query(Role).filter_by(key="tenant_owner", scope="tenant").one()
    db.query(TenantMember).filter_by(user_id=user.id).update({"role_id": owner.id})
    db.commit()
    authorize_as(client, user)
    assert client.get(path).status_code == 403


@pytest.mark.parametrize("missing_permission", ["tenant:manage", "user:read"])
def test_both_operator_permissions_are_required(
    client, db, system_context, missing_permission
):
    """組織管理とIdentity参照の双方のPermissionを検証する。"""
    from app.models.rbac import Permission

    role = db.query(Role).filter_by(key="system_admin", scope="system").one()
    permission = db.query(Permission).filter_by(code=missing_permission).one()
    db.query(RolePermission).filter_by(
        role_id=role.id, permission_id=permission.id
    ).delete()
    db.commit()
    assert client.get("/system/summary").status_code == 403


def test_operator_membership_does_not_change_business_permissions(
    client, db, system_context, create_test_project
):
    """兼任しても未参加Projectの業務内容にはアクセスできない。"""
    ctx = system_context
    role = db.query(Role).filter_by(key="tenant_member", scope="tenant").one()
    db.add(
        TenantMember(tenant_id=ctx["a"].id, user_id=ctx["operator"].id, role_id=role.id)
    )
    db.commit()
    project = create_test_project()
    project.tenant_id = ctx["a"].id
    db.commit()
    client.headers["X-Tenant-ID"] = str(ctx["a"].id)
    assert client.get("/tenants/current").status_code == 200
    assert client.get("/system/summary").status_code == 200
    assert client.get(f"/projects/{project.id}/tasks").status_code == 403


def test_empty_missing_invalid_pages_and_constant_query_count(
    client, db, system_context
):
    """空・存在しない対象・入力上限と組織数に依存しない取得を検証する。"""
    assert (
        client.get("/system/tenants", params={"q": "no-such-organization"}).json()[
            "items"
        ]
        == []
    )
    assert client.get("/system/tenants/999999").status_code == 404
    assert client.get("/system/tenants/999999/members").status_code == 404
    assert client.get("/system/users/999999/memberships").status_code == 404
    assert client.get("/system/tenants", params={"page_size": 101}).status_code == 422
    service = SystemManagementService()
    counts = []
    for size in [1, 100]:
        statements = []

        def executed(*args):
            statements.append(1)

        event.listen(db.bind, "after_cursor_execute", executed)
        try:
            service.tenants(db, page=1, page_size=size, q=None, status=None)
        finally:
            event.remove(db.bind, "after_cursor_execute", executed)
        counts.append(len(statements))
    assert counts[0] == counts[1] == 4
