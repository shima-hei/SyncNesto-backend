"""監査ログ閲覧の組織分離、管理権限、安全な応答と検索条件。"""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.audit_log import AuditLog
from app.models.project import Project
from app.models.rbac import Role
from app.models.tenant import Tenant, TenantMember
from tests.helpers.auth import authorize_as

URL = "/tenants/current/audit-logs"


@pytest.fixture
def context(client, db, create_test_user, create_test_project):
    """組織Ownerと、閲覧対象の現在組織を用意する。"""
    user = create_test_user(system_role="system_admin")
    tenant = db.query(Tenant).filter_by(slug="default").one()
    member = db.query(TenantMember).filter_by(user_id=user.id).one()
    member.display_name = "現在の組織の表示名"
    project = create_test_project(name="現在の案件")
    db.commit()
    authorize_as(client, user)
    return user, tenant, member, project


def log(
    db,
    tenant_id,
    *,
    event="task.updated",
    at=None,
    actor=None,
    project=None,
    metadata=None,
):
    """表示対象と比較用の監査記録を直接作成する。"""
    row = AuditLog(
        tenant_id=tenant_id,
        event_type=event,
        created_at=at or datetime.now(UTC),
        actor_user_id=actor,
        project_id=project,
        resource_type="task",
        resource_id=42,
        ip_address="192.0.2.42",
        user_agent="private-agent",
        request_id="trace",
        extra_metadata=metadata or {},
    )
    db.add(row)
    db.commit()
    return row


def test_current_tenant_only_and_safe_response(client, db, context):
    """別組織・Identity共通ログと自由記述のmetadataを公開しない。"""
    user, tenant, _, project = context
    other = Tenant(name="別組織", slug="other")
    db.add(other)
    db.commit()
    log(db, other.id, event="private.other")
    log(db, None, event="private.identity")
    row = log(
        db,
        tenant.id,
        actor=user.id,
        project=project.id,
        metadata={
            "password": "SECRET",
            "body": "SECRET",
            "title": "SECRET",
            "nested": {"token": "SECRET"},
            "ip_address": "SECRET",
            "updated_fields": ["body", "title", "SECRET", {"token": "SECRET"}],
            "before_role_key": "member",
            "after_role_key": "viewer",
            "version": 2,
        },
    )
    response = client.get(URL)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    item = response.json()["items"][0]
    assert item["id"] == row.id
    assert item["actor_name"] == "現在の組織の表示名"
    assert item["project_name"] == "現在の案件"
    assert item["details"] == {
        "updated_fields": ["body", "title"],
        "before_role_key": "member",
        "after_role_key": "viewer",
        "version": 2,
    }
    assert "SECRET" not in response.text
    assert "192.0.2.42" not in response.text
    assert "private-agent" not in response.text
    assert "request_id" not in item


@pytest.mark.parametrize(
    "role,expected",
    [
        ("tenant_owner", 200),
        ("tenant_admin", 200),
        ("tenant_member", 403),
    ],
)
def test_tenant_admin_required_even_for_system_admin(
    client, db, context, role, expected
):
    """運営権限だけでは組織の監査を閲覧できない。"""
    _, _, member, _ = context
    member.role_id = db.query(Role).filter_by(scope="tenant", key=role).one().id
    db.commit()
    assert client.get(URL).status_code == expected


def test_suspended_membership_and_foreign_tenant_rejected(client, db, context):
    """所属外のヘッダーと停止済み所属を拒否する。"""
    _, _, member, _ = context
    other = Tenant(name="別組織", slug="other")
    db.add(other)
    db.commit()
    assert client.get(URL, headers={"X-Tenant-ID": str(other.id)}).status_code == 403
    member.status = "suspended"
    db.commit()
    assert client.get(URL).status_code == 403


def test_unauthenticated_rejected(client):
    """Cookieなしで監査記録へアクセスできない。"""
    assert client.get(URL).status_code == 401


def test_filters_and_stable_pagination(client, db, context):
    """時刻が同じ記録もIDで安定し、終了日時を含めない。"""
    user, tenant, _, project = context
    at = datetime(2026, 10, 8, tzinfo=UTC)
    first = log(db, tenant.id, at=at, actor=user.id, project=project.id)
    second = log(db, tenant.id, at=at, actor=user.id, project=project.id)
    log(
        db,
        tenant.id,
        at=at,
        event="document.created",
        actor=user.id,
        project=project.id,
    )
    log(db, tenant.id, at=at + timedelta(days=1), actor=user.id, project=project.id)
    log(db, tenant.id, at=at - timedelta(seconds=1), actor=user.id, project=project.id)
    params = {
        "event_type": "task.updated",
        "actor_user_id": user.id,
        "project_id": project.id,
        "created_from": at.isoformat(),
        "created_before": (at + timedelta(days=1)).isoformat(),
        "page_size": 1,
    }
    response = client.get(URL, params=params).json()
    assert response["total"] == 2
    assert response["items"][0]["id"] == second.id
    assert (
        client.get(URL, params={**params, "page": 2}).json()["items"][0]["id"]
        == first.id
    )
    assert (
        client.get(URL, params={**params, "actor_user_id": user.id + 999}).json()[
            "total"
        ]
        == 0
    )
    assert (
        client.get(URL, params={**params, "project_id": project.id + 999}).json()[
            "total"
        ]
        == 0
    )


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page": 501},
        {"page_size": 51},
        {"actor_user_id": -1},
        {"project_id": 2**31},
        {"event_type": "x" * 101},
        {"created_from": "2026-10-08T00:00:00"},
    ],
)
def test_invalid_filters_rejected(client, context, params):
    """件数・整数・時刻の境界を検証する。"""
    assert client.get(URL, params=params).status_code == 422


def test_reversed_dates_rejected(client, context):
    """開始と終了が逆転した検索を拒否する。"""
    assert (
        client.get(
            URL,
            params={
                "created_from": "2026-10-09T00:00:00Z",
                "created_before": "2026-10-08T00:00:00Z",
            },
        ).status_code
        == 400
    )


def test_deleted_project_and_removed_actor_remain_readable(client, db, context):
    """対象の削除後も証跡を保持し、他組織の名称はjoinしない。"""
    user, tenant, member, project = context
    row = log(db, tenant.id, actor=user.id, project=project.id)
    project.deleted_at = datetime.now(UTC)
    db.commit()
    item = client.get(URL).json()["items"][0]
    assert item["project_name"] == "現在の案件"
    other = Tenant(name="別組織", slug="other")
    db.add(other)
    db.flush()
    other_project = Project(
        name="SECRET PROJECT", project_code="secret", tenant_id=other.id
    )
    db.add(other_project)
    db.flush()
    row.project_id = other_project.id
    row.actor_user_id = 99999
    db.commit()
    item = client.get(URL).json()["items"][0]
    assert item["project_name"] is None
    assert item["actor_name"] is None
    assert db.query(AuditLog).count() == 1
