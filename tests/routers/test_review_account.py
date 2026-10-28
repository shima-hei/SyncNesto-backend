"""審査用MFA例外を通常利用者・別組織へ拡張できないことを検証する。"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.core.exceptions import ForbiddenError
from app.core.mcp import PLUGIN_CLIENT_ID
from app.models.mcp import McpConnection
from app.models.project import ProjectMember
from app.models.rbac import Role, UserRole
from app.models.review_account import ReviewAccountGrant
from app.models.session import UserSession
from app.models.tenant import Tenant, TenantMember
from app.models.user import User
from app.services.account_action import AccountActionService
from app.services.review_account import ReviewAccountService
from app.services.review_fixture import ReviewFixtureService
from tests.routers.test_mcp import api_headers, connect, operate


@pytest.fixture
def review(client, db, monkeypatch):
    """本番と同じMFA必須条件で専用Identityを発行し、パスワードでログインする。"""
    monkeypatch.setattr(settings, "mfa_required", True)
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_issuer_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(settings, "mcp_resource_url", "http://127.0.0.1:8765/mcp")
    fixture = ReviewFixtureService().create(
        db, "review-password-12345", datetime.now(UTC) + timedelta(days=30)
    )
    db.commit()
    response = client.post(
        "/auth/login",
        json={
            "email": fixture["email"],
            "password": "review-password-12345",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["next_step"] == "authenticated"
    client.headers[settings.csrf_header_name] = client.cookies.get(
        settings.csrf_cookie_name
    )
    return fixture


def test_review_login_preserves_normal_mfa(client, db, review, create_test_user):
    """審査だけがパスワードで利用でき、MFA成功の時刻は捏造しない。"""
    result = client.get("/auth/me")
    assert result.status_code == 200
    assert result.json()["review_access_expires_at"] == review["expires_at"].replace(
        "+00:00", "Z"
    )
    assert result.json()["demo"] is None
    assert result.json()["system_roles"] == []
    session = db.query(UserSession).filter_by(user_id=review["user_id"]).one()
    assert session.mfa_verified_at is None
    assert session.mfa_method is None
    normal = create_test_user()
    response = client.post(
        "/auth/login", json={"email": normal.email, "password": "password123"}
    )
    assert response.status_code == 200
    assert response.json()["next_step"] == "mfa_setup"
    assert client.get("/auth/me").status_code == 401


def test_review_limits_web_identity_and_management(client, db, review):
    """組織固定・管理拒否・認証情報変更の禁止をHTTP境界で確認する。"""
    assert client.get("/tenants").status_code == 200
    assert [t["id"] for t in client.get("/tenants").json()] == [review["tenant_id"]]
    default = db.query(Tenant).filter_by(slug="default").one()
    assert (
        client.get("/projects", headers={"X-Tenant-ID": str(default.id)}).status_code
        == 403
    )
    assert client.get("/users").status_code == 403
    assert client.get("/tenants/management").status_code == 403
    assert (
        client.patch("/auth/me", json={"name": "changed", "version": 1}).status_code
        == 403
    )
    assert client.post("/auth/mfa/manage").status_code == 403
    assert (
        client.post(
            "/auth/mfa/reauth", json={"password": "review-password-12345"}
        ).status_code
        == 403
    )
    user = db.get(User, review["user_id"])
    assert user is not None
    with pytest.raises(ForbiddenError):
        AccountActionService()._request_user(db, user.id, user.id, None)


@pytest.mark.parametrize(
    "change",
    [
        "expired",
        "revoked",
        "system_role",
        "extra_tenant",
        "tenant_admin",
        "extra_project",
        "other_member",
    ],
)
def test_review_invalid_state_fails_closed(
    client, db, review, create_test_user, create_test_project, change
):
    """許可条件が変われば既存CookieとMCP access・refreshを全て拒否する。"""
    direct, tokens, _ = connect(client, review["consented_project_id"])
    headers = api_headers(direct, tokens["access_token"])
    grant = db.get(ReviewAccountGrant, review["user_id"])
    assert grant is not None
    if change == "expired":
        grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif change == "revoked":
        ReviewFixtureService().revoke(db, review["user_id"])
    elif change == "system_role":
        role = db.query(Role).filter_by(key="system_admin", scope="system").one()
        db.add(UserRole(user_id=review["user_id"], role_id=role.id))
    elif change == "extra_tenant":
        default = db.query(Tenant).filter_by(slug="default").one()
        role = db.query(Role).filter_by(key="tenant_member", scope="tenant").one()
        db.add(
            TenantMember(
                user_id=review["user_id"], tenant_id=default.id, role_id=role.id
            )
        )
    elif change == "tenant_admin":
        member = db.query(TenantMember).filter_by(user_id=review["user_id"]).one()
        member.role_id = (
            db.query(Role).filter_by(key="tenant_admin", scope="tenant").one().id
        )
    elif change == "extra_project":
        project = create_test_project()
        role = db.query(Role).filter_by(key="manager", scope="project").one()
        db.add(
            ProjectMember(
                user_id=review["user_id"], project_id=project.id, role_id=role.id
            )
        )
    else:
        normal = create_test_user()
        role = db.query(Role).filter_by(key="tenant_member", scope="tenant").one()
        db.add(
            TenantMember(
                user_id=normal.id, tenant_id=review["tenant_id"], role_id=role.id
            )
        )
    db.commit()
    assert client.get("/auth/me").status_code in {401, 403}
    assert (
        direct.post(
            "/oauth/exchange",
            headers={"Authorization": "Bearer " + tokens["access_token"]},
        ).status_code
        == 401
    )
    assert direct.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": "syncnesto-codex-local",
            "resource": settings.mcp_resource_url,
            "refresh_token": tokens["refresh_token"],
        },
    ).status_code in {400, 401}
    assert (
        operate(
            direct, headers, "list_projects", review["consented_project_id"], key=False
        ).status_code
        == 401
    )


def test_review_oauth_limits_consent_and_real_writes(client, db, review):
    """既存OAuthと実業務ツールを使い、許可Project以外を公開しない。"""
    direct, tokens, _ = connect(client, review["consented_project_id"])
    headers = api_headers(direct, tokens["access_token"])
    connection = db.query(McpConnection).filter_by(user_id=review["user_id"]).one()
    grant = db.get(ReviewAccountGrant, review["user_id"])
    assert grant is not None and connection.expires_at <= grant.expires_at
    assert connection.mfa_verified_at is None
    assert (
        operate(
            direct,
            headers,
            "create_requirement_document",
            review["consented_project_id"],
            data={"title": "Created by reviewer", "document_code": "REVIEW-WRITE"},
        ).status_code
        == 200
    )
    assert (
        operate(
            direct,
            headers,
            "get_requirement_target",
            review["unconsented_project_id"],
            target_type="requirement_item",
            target_id=review["unconsented_requirement_id"],
            key=False,
        ).status_code
        == 403
    )
    # 閲覧専用ProjectはMCP連携候補に含めない。
    from urllib.parse import parse_qs, urlsplit

    from tests.routers.test_mcp import begin

    response, _, _ = begin(client)
    request_id = parse_qs(urlsplit(response.headers["location"]).query)["request_id"][0]
    choices = client.get(
        "/integrations/mcp/authorization-requests/" + request_id
    ).json()["projects"]
    assert {p["id"] for p in choices} == {
        review["consented_project_id"],
        review["unconsented_project_id"],
    }


def test_review_logout_does_not_destroy_fixture(client, db, review):
    """匿名デモと異なり、審査の再接続に必要なサンプルを保持する。"""
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/auth/me").status_code == 401
    assert db.get(ReviewAccountGrant, review["user_id"]) is not None


def test_review_public_mcp_can_use_documents_context_and_comments(
    client, review, monkeypatch
):
    """隔離された審査guestの新操作を、実際の公開OAuth経路で検証する。"""
    for name in (
        "mcp_plugin_extended_tools_enabled",
        "mcp_read_context_enabled",
        "mcp_task_comments_enabled",
        "project_objectives_enabled",
    ):
        monkeypatch.setattr(settings, name, True)
    project_id = review["consented_project_id"]
    direct, tokens, _ = connect(client, project_id, client_id=PLUGIN_CLIENT_ID)
    headers = api_headers(direct, tokens["access_token"])
    catalog = direct.get("/integrations/mcp/catalog", headers=headers)
    assert catalog.status_code == 200, catalog.text
    assert len(catalog.json()["tools"]) == 41
    created = operate(
        direct,
        headers,
        "create_project_document",
        project_id,
        data={"title": "Review notes", "body": "Draft"},
    )
    assert created.status_code == 200, created.text
    document_id = created.json()["id"]
    outline = operate(
        direct,
        headers,
        "get_project_document_content",
        project_id,
        document_id=document_id,
        key=False,
    )
    assert outline.status_code == 200, outline.text
    patched = operate(
        direct,
        headers,
        "patch_project_document",
        project_id,
        document_id=document_id,
        version=outline.json()["version"],
        edits=[
            {"start": 0, "end": 5, "expected_text": "Draft", "replacement": "Reviewed"}
        ],
    )
    assert patched.status_code == 200, patched.text
    assert (
        operate(
            direct,
            headers,
            "get_project_document",
            review["unconsented_project_id"],
            document_id=document_id,
            key=False,
        ).status_code
        == 403
    )
    brief = operate(direct, headers, "get_project_brief", project_id, key=False)
    assert brief.status_code == 200, brief.text
    task = operate(
        direct, headers, "create_task", project_id, data={"title": "Review task"}
    )
    assert task.status_code == 200, task.text
    comment = operate(
        direct,
        headers,
        "create_task_comment",
        project_id,
        task_id=task.json()["id"],
        data={"body": "Review complete"},
    )
    assert comment.status_code == 200, comment.text
    comments = operate(
        direct,
        headers,
        "list_task_comments",
        project_id,
        task_id=task.json()["id"],
        key=False,
    )
    assert comments.status_code == 200, comments.text
    assert comments.json()["total"] == 1


@pytest.mark.parametrize(
    "change", ["ordinary_guest", "wrong_tenant", "expired", "revoked", "demo"]
)
def test_extended_mcp_actor_guard_keeps_review_boundaries(
    db, review, create_test_user, monkeypatch, change
):
    """許可行の存在だけでguest/別組織/失効/デモを通さない。"""
    user = db.get(User, review["user_id"])
    if change == "ordinary_guest":
        user = create_test_user()
        user.user_type = "guest"
        db.commit()
    monkeypatch.setitem(db.info, "tenant_id", review["tenant_id"])
    if change == "wrong_tenant":
        monkeypatch.setitem(db.info, "tenant_id", review["tenant_id"] + 1000)
    elif change == "demo":
        monkeypatch.setitem(db.info, "data_realm", "demo")
    elif change in {"expired", "revoked"}:
        grant = db.get(ReviewAccountGrant, user.id)
        if change == "expired":
            grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        else:
            grant.revoked_at = datetime.now(UTC)
        db.commit()
    with pytest.raises(ForbiddenError):
        ReviewAccountService().require_mcp_actor(db, user)
