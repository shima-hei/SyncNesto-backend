"""組織管理、所属取消し、全業務Routerの境界とIDORの回帰検証。"""

import re
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.exceptions import ForbiddenError
from app.db.tenant_scope import scoped_models
from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.models.project import Project, ProjectMember
from app.models.rbac import Role
from app.models.session import UserSession
from app.models.task import Task, TaskComment
from app.models.tenant import Tenant, TenantMember
from app.models.test_design import TestCase as CaseModel
from app.models.test_design import TestDesign as DesignModel
from app.models.user import User
from tests.helpers.auth import authorize_as


@pytest.mark.parametrize("action", ["add", "change", "remove"])
def test_project_membership_changes_preserve_other_tenant_sessions(
    client, db, tenant_context, assign_project_role, action
):
    """組織Aの権限変更を既存セッションに即時反映し、組織Bのログインを保持する。"""
    from fastapi.testclient import TestClient

    ctx = tenant_context
    target = ctx["outsider"]
    assign_project_role(user=target, project=ctx["pb"], role_key="member")
    member = None
    if action != "add":
        member = assign_project_role(user=target, project=ctx["pa"], role_key="member")
    with TestClient(client.app) as target_client:
        session_id = authorize_as(target_client, target)
        target_client.headers["X-Tenant-ID"] = str(ctx["b"].id)
        assert target_client.get(f"/tasks/{ctx['task'].id}").status_code == 200
        if action == "add":
            response = client.post(
                f"/projects/{ctx['pa'].id}/members",
                json={"user_id": target.id, "role_key": "member"},
            )
            assert response.status_code == 201
        elif action == "change":
            assert member is not None
            response = client.patch(
                f"/projects/{ctx['pa'].id}/members/{target.id}",
                json={"role_key": "viewer", "version": member.version},
            )
            assert response.status_code == 200
        else:
            response = client.delete(f"/projects/{ctx['pa'].id}/members/{target.id}")
            assert response.status_code == 204
        session = db.get(UserSession, session_id)
        assert session is not None
        db.refresh(session)
        assert session.revoked_at is None
        assert target_client.get(f"/tasks/{ctx['task'].id}").status_code == 200
        target_client.headers["X-Tenant-ID"] = str(ctx["a"].id)
        response = target_client.post(
            f"/projects/{ctx['pa'].id}/tasks", json={"title": "Current permission"}
        )
        assert response.status_code == (201 if action == "add" else 403)


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/tasks/{task}", None),
        ("PATCH", "/tasks/{task}", {"version": 1, "title": "After deletion"}),
        ("DELETE", "/tasks/{task}", None),
        ("GET", "/tasks/{task}/comments", None),
        ("POST", "/tasks/{task}/comments", {"body": "After deletion"}),
        ("GET", "/projects/{pb}/overview", None),
    ],
)
def test_deleted_project_denies_business_access(
    client, db, tenant_context, method, path, body
):
    """案件の削除後はProject所属が残っていても直接IDから業務データにアクセスできない。"""
    ctx = tenant_context
    client.headers["X-Tenant-ID"] = str(ctx["b"].id)
    assert client.delete(f"/projects/{ctx['pb'].id}").status_code == 204
    values = {key: value.id for key, value in ctx.items()}
    response = client.request(method, path.format(**values), json=body)
    assert response.status_code in (403, 404)
    db.expire_all()
    assert db.get(Task, ctx["task"].id).title == "Secret Task B"
    assert db.get(Task, ctx["task"].id).deleted_at is None


@pytest.mark.parametrize("action", ["change", "remove"])
@pytest.mark.parametrize("state", ["tenant_suspended", "identity_inactive"])
def test_last_active_project_admin_ignores_inactive_memberships(
    client, db, tenant_context, assign_project_role, action, state
):
    """停止済みの組織所属・Identityを数えて、唯一の有効Project管理者を失わせない。"""
    ctx = tenant_context
    assign_project_role(
        user=ctx["outsider"], project=ctx["pa"], role_key="project_admin"
    )
    if state == "tenant_suspended":
        member = (
            db.query(TenantMember)
            .filter(
                TenantMember.tenant_id == ctx["a"].id,
                TenantMember.user_id == ctx["outsider"].id,
            )
            .one()
        )
        member.status = "suspended"
    else:
        ctx["outsider"].is_active = False
    db.commit()
    endpoint = f"/projects/{ctx['pa'].id}/members/{ctx['user'].id}"
    if action == "change":
        response = client.patch(endpoint, json={"role_key": "viewer", "version": 1})
    else:
        response = client.delete(endpoint)
    assert response.status_code == 409
    assert response.json()["code"] == "LAST_PROJECT_ADMIN_REQUIRED"


def test_inactive_project_admin_can_be_removed_when_active_admin_remains(
    client, db, tenant_context, assign_project_role
):
    """唯一の有効な管理者を残して、停止中の旧管理者の所属を整理できる。"""
    ctx = tenant_context
    assign_project_role(
        user=ctx["outsider"], project=ctx["pa"], role_key="project_admin"
    )
    member = (
        db.query(TenantMember)
        .filter(
            TenantMember.tenant_id == ctx["a"].id,
            TenantMember.user_id == ctx["outsider"].id,
        )
        .one()
    )
    member.status = "suspended"
    db.commit()
    response = client.delete(f"/projects/{ctx['pa'].id}/members/{ctx['outsider'].id}")
    assert response.status_code == 204


def test_concurrent_project_admin_removal_preserves_one_admin(
    client, db, tenant_context, assign_project_role, monkeypatch
):
    """異なる管理者への並行削除をProject単位で直列化する。"""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    from fastapi.testclient import TestClient

    from app.services.project import ProjectMemberService

    ctx = tenant_context
    assign_project_role(
        user=ctx["outsider"], project=ctx["pa"], role_key="project_admin"
    )
    first_checked, second_checked, release_first = Event(), Event(), Event()
    order_lock = Lock()
    call_count = 0
    original = ProjectMemberService._ensure_project_admin_remains

    def checked(self, *args, **kwargs):
        nonlocal call_count
        original(self, *args, **kwargs)
        with order_lock:
            call_count += 1
            first_call = call_count == 1
        if first_call:
            first_checked.set()
            assert release_first.wait(timeout=10)
        else:
            second_checked.set()

    monkeypatch.setattr(ProjectMemberService, "_ensure_project_admin_remains", checked)
    with TestClient(client.app) as second_client:
        authorize_as(second_client, ctx["user"])
        second_client.headers["X-Tenant-ID"] = str(ctx["a"].id)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                client.delete,
                f"/projects/{ctx['pa'].id}/members/{ctx['outsider'].id}",
            )
            try:
                assert first_checked.wait(timeout=5)
                second = executor.submit(
                    second_client.delete,
                    f"/projects/{ctx['pa'].id}/members/{ctx['user'].id}",
                )
                # ロックがなければ両リクエストが同じcount=2を検証するまで待つ。
                second_checked.wait(timeout=0.5)
            finally:
                release_first.set()
            assert first.result(timeout=10).status_code == 204
            assert second.result(timeout=10).status_code == 409
    db.expire_all()
    assert (
        db.query(ProjectMember)
        .filter(
            ProjectMember.project_id == ctx["pa"].id,
            ProjectMember.deleted_at.is_(None),
        )
        .count()
        == 1
    )


@pytest.fixture
def tenant_context(
    client,
    db,
    create_test_user,
    create_test_project,
    assign_project_role,
    create_test_requirement_document,
    create_test_requirement,
):
    """同じ本人が両組織のProject管理者でも現在組織の境界を越えられない状況。"""
    user = create_test_user(email="owner@example.com", system_role="system_admin")
    outsider = create_test_user(email="other@example.com")
    a = db.query(Tenant).filter(Tenant.slug == "default").one()
    b = Tenant(name="Secret organization B", slug="b")
    db.add(b)
    db.flush()
    owner = db.query(Role).filter(Role.key == "tenant_owner").one()
    db.add(TenantMember(tenant_id=b.id, user_id=user.id, role_id=owner.id))
    db.add(TenantMember(tenant_id=b.id, user_id=outsider.id, role_id=owner.id))
    pa = create_test_project(project_code="SAME", name="Project A")
    pb = Project(tenant_id=b.id, project_code="SAME", name="Secret Project B")
    db.add(pb)
    db.commit()
    for project in (pa, pb):
        assign_project_role(user=user, project=project, role_key="project_admin")
    doc = create_test_requirement_document(project=pb)
    requirement = create_test_requirement(document=doc)
    task = Task(
        project_id=pb.id, task_code="SECRET", title="Secret Task B", assignee_id=user.id
    )
    design = DesignModel(
        project_id=pb.id, name="Secret Design B", created_by=user.id, updated_by=user.id
    )
    db.add_all([task, design])
    db.flush()
    comment = TaskComment(task_id=task.id, created_by=user.id, body="Secret Comment B")
    case = CaseModel(
        id=uuid4(), design_id=design.id, position=0, source={}, source_hash="x"
    )
    notification = Notification(
        tenant_id=b.id,
        recipient_user_id=user.id,
        type="assigned",
        project_id=pb.id,
        target_type="task",
        target_id=str(task.id),
        event_key="secret-b",
        snapshot={
            "actor_name": "B",
            "project_name": pb.name,
            "target_title": task.title,
        },
        context={},
    )
    db.add_all([comment, case, notification])
    db.commit()
    authorize_as(client, user)
    client.headers["X-Tenant-ID"] = str(a.id)
    return dict(
        user=user,
        outsider=outsider,
        a=a,
        b=b,
        pa=pa,
        pb=pb,
        doc=doc,
        requirement=requirement,
        task=task,
        design=design,
        comment=comment,
        case=case,
        notification=notification,
    )


def test_all_business_routes_require_tenant_context(client):
    """新しい業務APIにも共通Dependencyを必須とし、手書きチェックの抜けを防ぐ。"""

    count = 0
    for path, operations in client.app.openapi()["paths"].items():
        if path == "/" or path.startswith(("/auth", "/users", "/health", "/tenants")):
            continue
        for operation in operations.values():
            assert any(
                parameter["name"] == "X-Tenant-ID" and parameter["in"] == "header"
                for parameter in operation.get("parameters", [])
            ), path
            count += 1
    assert count >= 130


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/projects/{pb}", None),
        ("PATCH", "/projects/{pb}", {"version": 1, "name": "intrusion"}),
        ("DELETE", "/projects/{pb}", None),
        ("GET", "/projects/{pb}/overview", None),
        ("GET", "/projects/{pb}/tasks?q=Secret", None),
        ("POST", "/projects/{pb}/tasks", {"title": "intrusion"}),
        ("GET", "/tasks/{task}", None),
        ("PATCH", "/tasks/{task}", {"version": 1, "title": "intrusion"}),
        ("DELETE", "/tasks/{task}", None),
        ("GET", "/tasks/{task}/comments", None),
        ("POST", "/tasks/{task}/comments", {"body": "intrusion"}),
        ("PATCH", "/task-comments/{comment}", {"version": 1, "body": "intrusion"}),
        ("DELETE", "/task-comments/{comment}", None),
        ("GET", "/projects/{pb}/requirements/{requirement}", None),
        (
            "PATCH",
            "/projects/{pb}/requirements/{requirement}",
            {"version": 1, "title": "intrusion"},
        ),
        ("DELETE", "/projects/{pb}/requirements/{requirement}", None),
        (
            "POST",
            "/projects/{pb}/requirement-documents/{doc}/exports",
            {"format": "markdown"},
        ),
        ("GET", "/projects/{pb}/test-designs/{design}", None),
        ("POST", "/projects/{pb}/test-designs", {"name": "intrusion"}),
        (
            "PUT",
            "/projects/{pb}/test-designs/{design}",
            {
                "version": 1,
                "name": "intrusion",
                "items": [],
                "factors": [],
                "levels": [],
                "patterns": [],
                "values": [],
                "links": [],
                "columns": [],
                "layout": {},
            },
        ),
        ("DELETE", "/projects/{pb}/test-designs/{design}", None),
        ("GET", "/projects/{pb}/test-designs/{design}/cases", None),
        (
            "PATCH",
            "/projects/{pb}/test-designs/{design}/cases/{case}",
            {"version": 1, "status": "passed"},
        ),
        ("POST", "/projects/{pb}/test-designs/{design}/cases/generate", {"version": 1}),
        ("GET", "/projects/{pb}/test-designs/{design}/comments", None),
        ("GET", "/projects/{pb}/test-designs/{design}/cases/{case}/executions", None),
        (
            "POST",
            "/projects/{pb}/test-designs/{design}/cases/{case}/executions/{execution}/evidence/upload-plan",
            {"filename": "b.txt", "content_type": "text/plain", "size_bytes": 1},
        ),
        ("POST", "/projects/{pb}/members", {"user_id": 1, "role_key": "project_admin"}),
        ("POST", "/notifications/{notification}/read", None),
    ],
)
def test_cross_tenant_id_operations(client, db, tenant_context, method, path, body):
    """両方に所属していても選択中の組織から他組織のIDを使う全methodを拒否する。"""
    ctx = tenant_context
    identifiers = {key: value.id for key, value in ctx.items()}
    identifiers["execution"] = uuid4()
    url = path.format(**identifiers)
    assert any(
        method.lower() in operations
        and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), url.split("?")[0])
        for template, operations in client.app.openapi()["paths"].items()
    ), url
    response = client.request(method, url, json=body)
    assert response.status_code in (403, 404), response.text
    assert "Secret" not in response.text
    db.refresh(ctx["task"])
    db.refresh(ctx["pb"])
    assert ctx["task"].title == "Secret Task B"
    assert ctx["pb"].deleted_at is None


def test_lists_home_notifications_and_selection(client, tenant_context):
    """一覧・検索・集計・一括既読を現在組織だけに限定する。"""
    ctx = tenant_context
    assert client.get("/projects").json()["total"] == 1
    assert client.get("/projects?q=Secret").json()["total"] == 0
    assert client.get("/notifications").json()["total"] == 0
    assert client.get("/notifications/unread-count").json()["count"] == 0
    assert client.post("/notifications/read-all").json()["updated_count"] == 0
    assert client.get("/home/tasks").json()["summary"]["total"] == 0
    assert client.get("/home/projects").json()["total"] == 1
    del client.headers["X-Tenant-ID"]
    assert client.get("/projects").status_code == 400
    client.headers["X-Tenant-ID"] = "99999"
    assert client.get("/projects").status_code == 403
    client.headers["X-Tenant-ID"] = str(ctx["b"].id)
    assert client.get("/projects").json()["items"][0]["id"] == ctx["pb"].id
    assert client.get("/notifications").json()["total"] == 1
    assert client.get("/notifications/unread-count").json()["count"] == 1


def test_forged_body_and_membership_removal(client, db, tenant_context):
    """bodyから所有者を決めず、取消しは同じ組織のProject所属だけに反映する。"""
    ctx = tenant_context
    response = client.post(
        "/projects",
        json={"tenant_id": ctx["b"].id, "project_code": "FORGED", "name": "A"},
    )
    assert response.status_code in (201, 422)
    if response.status_code == 201:
        assert db.get(Project, response.json()["id"]).tenant_id == ctx["a"].id
    authorize_as(client, ctx["outsider"])
    client.headers["X-Tenant-ID"] = str(ctx["b"].id)
    member = (
        db.query(TenantMember)
        .filter(
            TenantMember.tenant_id == ctx["b"].id,
            TenantMember.user_id == ctx["user"].id,
        )
        .one()
    )
    assert (
        client.delete(
            f"/tenants/current/members/{ctx['user'].id}",
            params={"version": member.version},
        ).status_code
        == 204
    )
    db.expire_all()
    assert (
        db.query(ProjectMember)
        .filter(
            ProjectMember.project_id == ctx["pb"].id,
            ProjectMember.user_id == ctx["user"].id,
        )
        .one()
        .deleted_at
        is not None
    )
    assert (
        db.query(ProjectMember)
        .filter(
            ProjectMember.project_id == ctx["pa"].id,
            ProjectMember.user_id == ctx["user"].id,
        )
        .one()
        .deleted_at
        is None
    )
    authorize_as(client, ctx["user"])
    assert client.get("/projects").status_code == 403


def test_tenant_roles_owner_protection_and_local_profile(client, db, tenant_context):
    """Ownerは最後の本人を失わず、管理者はOwnerへ昇格できず、Identity変更を拒否する。"""
    ctx = tenant_context
    original = db.get(User, ctx["outsider"].id).name
    members = client.get("/tenants/current/members").json()
    own = next(member for member in members if member["user_id"] == ctx["user"].id)
    endpoint = f"/tenants/current/members/{ctx['user'].id}"
    assert (
        client.patch(
            endpoint, json={"version": own["version"], "status": "suspended"}
        ).status_code
        == 409
    )
    assert (
        client.patch(
            endpoint, json={"version": own["version"], "role_key": "tenant_member"}
        ).status_code
        == 409
    )
    other = next(
        member for member in members if member["user_id"] == ctx["outsider"].id
    )
    endpoint = f"/tenants/current/members/{ctx['outsider'].id}"
    assert (
        client.patch(
            endpoint,
            json={
                "version": other["version"],
                "display_name": "Local A",
                "role_key": "tenant_admin",
            },
        ).status_code
        == 200
    )
    db.expire_all()
    assert db.get(User, ctx["outsider"].id).name == original
    audit = (
        db.query(AuditLog).filter(AuditLog.event_type == "tenant.member_updated").one()
    )
    assert audit.tenant_id == ctx["a"].id
    assert audit.actor_user_id == ctx["user"].id
    assert audit.target_user_id == ctx["outsider"].id
    assert audit.extra_metadata == {
        "role_before": "tenant_member",
        "role_after": "tenant_admin",
        "status_before": "active",
        "status_after": "active",
    }
    b_member = (
        db.query(TenantMember)
        .filter(
            TenantMember.tenant_id == ctx["b"].id,
            TenantMember.user_id == ctx["outsider"].id,
        )
        .one()
    )
    assert b_member.display_name != "Local A"
    authorize_as(client, ctx["outsider"])
    assert (
        client.patch(
            endpoint, json={"version": other["version"] + 1, "role_key": "tenant_owner"}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            endpoint, json={"version": other["version"] + 1, "password": "bad"}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            endpoint, json={"version": other["version"] + 1, "email": "bad@example.com"}
        ).status_code
        == 422
    )


def test_new_identity_exact_add_and_operator_separation(client, db, tenant_context):
    """登録は原子的でメール完全一致のみ、運営者の業務自動許可は存在しない。"""
    ctx = tenant_context
    created = client.post(
        "/tenants/current/users",
        json={"email": "new@example.com", "display_name": "New"},
    )
    assert created.status_code == 201, created.text
    password = created.json()["initial_password"]
    assert len(password) >= 32
    assert "initial_password" not in client.get("/tenants/current/members").text
    assert (
        client.post(
            "/tenants/current/users",
            json={"email": "new@example.com", "display_name": "Duplicate"},
        ).status_code
        == 409
    )
    assert (
        client.post("/tenants/current/members", json={"email": "new"}).status_code
        == 422
    )
    assert (
        client.post(
            "/tenants/current/members", json={"email": "unknown@example.com"}
        ).status_code
        == 404
    )
    db.query(ProjectMember).filter(ProjectMember.user_id == ctx["user"].id).update(
        {"deleted_at": datetime.now(UTC)}
    )
    db.commit()
    assert client.get(f"/projects/{ctx['pa'].id}/tasks").status_code == 403
    assert client.get(f"/projects/{ctx['pa'].id}/overview").status_code == 403
    assert client.get("/system/tenants").status_code == 404
    assert client.get("/tenants/management").status_code == 200


def test_orm_read_update_delete_and_reference_guards(db, tenant_context):
    """Repositoryの条件漏れ・更新・削除・参照先の混入にも共通防御を適用する。"""
    ctx = tenant_context
    b_task = ctx["task"].id
    a_project = ctx["pa"].id
    db.expunge_all()
    db.info.update(tenant_id=ctx["a"].id, tenant_user_id=ctx["user"].id)
    assert db.scalar(select(Task).where(Task.id == b_task)) is None
    assert db.query(Task).filter(Task.id == b_task).update({"title": "bad"}) == 0
    assert db.query(Task).filter(Task.id == b_task).delete() == 0
    db.add(
        Task(project_id=a_project, task_code="BAD", title="bad", parent_task_id=b_task)
    )
    with pytest.raises(ForbiddenError):
        db.flush()
    db.rollback()
    assert len(scoped_models()) >= 40


def test_every_business_operation_rejects_unowned_tenant(client, create_test_user):
    """すべての既存HTTP operationで、存在しない組織の選択を先に拒否する。"""
    authorize_as(client, create_test_user())
    client.headers["X-Tenant-ID"] = "999999"
    count = 0
    for path, operations in client.app.openapi()["paths"].items():
        if path == "/" or path.startswith(("/auth", "/users", "/health", "/tenants")):
            continue
        for method, operation in operations.items():
            url = path
            for parameter in operation.get("parameters", []):
                if parameter["in"] == "path":
                    value = (
                        str(uuid4())
                        if parameter["schema"].get("format") == "uuid"
                        else "1"
                    )
                    url = url.replace("{" + parameter["name"] + "}", value)
            response = client.request(
                method.upper(), url, json={} if method != "get" else None
            )
            assert response.status_code == 403, (method, url, response.text)
            count += 1
    assert count >= 130


def test_operator_creates_one_explicit_owner_and_audit(client, db, create_test_user):
    """運営者は指定した本人だけをOwnerにし、自分の所属を自動追加しない。"""
    operator = create_test_user(
        email="operator@example.com", system_role="system_admin"
    )
    owner = create_test_user(email="designated@example.com")
    authorize_as(client, operator)
    body = {"name": "New organization", "slug": "new-org", "owner_email": owner.email}
    response = client.post("/tenants", json=body)
    assert response.status_code == 201, response.text
    tenant_id = response.json()["id"]
    members = db.query(TenantMember).filter(TenantMember.tenant_id == tenant_id).all()
    assert len(members) == 1 and members[0].user_id == owner.id
    assert db.get(Role, members[0].role_id).key == "tenant_owner"
    audit = db.query(AuditLog).filter(AuditLog.event_type == "tenant.created").one()
    assert audit.tenant_id == tenant_id and audit.actor_user_id == operator.id
    assert audit.target_user_id == owner.id and audit.extra_metadata == {}
    assert client.post("/tenants", json=body).status_code == 409
    assert len(client.get("/tenants/management").json()) == 2
    client.headers["X-Tenant-ID"] = str(tenant_id)
    assert client.get("/tenants/current").status_code == 403
    authorize_as(client, owner)
    assert client.get("/tenants/current").status_code == 200
    assert (
        client.post("/tenants", json={**body, "slug": "not-allowed"}).status_code == 403
    )
    assert client.get("/tenants/management").status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/projects/{pa}/requirements/{requirement}",
        "/projects/{pa}/test-designs/{design}",
        "/projects/{pa}/test-designs/{design}/cases/{case}/executions",
    ],
)
def test_foreign_child_id_under_authorized_parent(client, tenant_context, path):
    """認可済みの親IDと他組織の子IDを組み合わせても内容を取得できない。"""
    response = client.get(
        path.format(**{key: value.id for key, value in tenant_context.items()})
    )
    assert response.status_code in (403, 404), response.text
    assert "Secret" not in response.text


@pytest.mark.parametrize("state", ["unowned", "suspended", "removed", "inactive"])
def test_existing_tenant_selection_requires_active_membership(
    client, db, tenant_context, create_test_user, state
):
    """存在する組織IDでも、所属なし・所属停止/削除・組織停止を拒否する。"""
    ctx = tenant_context
    if state == "unowned":
        user = create_test_user(email="only-a@example.com")
    else:
        user = ctx["user"]
        if state == "inactive":
            ctx["b"].status = "suspended"
        else:
            membership = (
                db.query(TenantMember)
                .filter(
                    TenantMember.tenant_id == ctx["b"].id,
                    TenantMember.user_id == user.id,
                )
                .one()
            )
            membership.status = state
        db.commit()
    authorize_as(client, user)
    client.headers["X-Tenant-ID"] = str(ctx["b"].id)
    assert client.get(f"/projects/{ctx['pb'].id}").status_code == 403
    assert (
        client.post(
            f"/projects/{ctx['pb'].id}/tasks", json={"title": "bad"}
        ).status_code
        == 403
    )
    assert all(choice["id"] != ctx["b"].id for choice in client.get("/tenants").json())


def test_new_user_references_stay_in_tenant_and_historical_references_survive(
    client, db, tenant_context
):
    """共通IdentityのID指定で他組織の担当者を新規参照せず、所属取消し後の履歴は保持する。"""
    ctx = tenant_context
    response = client.post(
        f"/projects/{ctx['pa'].id}/tasks",
        json={"title": "Existing assignment", "assignee_id": ctx["outsider"].id},
    )
    assert response.status_code == 201, response.text
    task = response.json()
    membership = (
        db.query(TenantMember)
        .filter(
            TenantMember.tenant_id == ctx["a"].id,
            TenantMember.user_id == ctx["outsider"].id,
        )
        .one()
    )
    membership.status = "removed"
    db.commit()
    response = client.patch(
        f"/tasks/{task['id']}",
        json={"version": task["version"], "title": "Preserved assignment"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["assignee_id"] == ctx["outsider"].id
    for field in ("assignee_id", "reporter_id"):
        response = client.post(
            f"/projects/{ctx['pa'].id}/tasks",
            json={"title": "Foreign identity", field: ctx["outsider"].id},
        )
        assert response.status_code == 403, response.text
    allowed = client.post(
        f"/projects/{ctx['pa'].id}/tasks", json={"title": "Unassigned"}
    )
    assert allowed.status_code == 201
    assert (
        client.patch(
            f"/tasks/{allowed.json()['id']}",
            json={
                "version": allowed.json()["version"],
                "assignee_id": ctx["outsider"].id,
            },
        ).status_code
        == 403
    )
