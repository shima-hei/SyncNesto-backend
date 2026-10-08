"""横断検索の検索語・ページ・種類別権限・組織境界を検証する。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.config import settings
from app.models.demo import DemoSession
from app.models.document import ProjectDocument
from app.models.project import Project, ProjectMember
from app.models.rbac import Permission, Role, RolePermission
from app.models.requirement import Requirement, RequirementDocument
from app.models.task import Task
from app.models.tenant import Tenant, TenantMember
from app.models.test_design import (
    TestCase as Case,
)
from app.models.test_design import (
    TestDesign as Design,
)
from app.models.test_design import (
    TestItem as Item,
)
from app.repositories.rbac import RbacRepository
from tests.helpers.auth import authorize_as


@pytest.fixture
def context(client, db, create_test_user, create_test_project, assign_project_role):
    """全種類の本文に共通語がある案件と、所属外案件を用意する。"""
    user = create_test_user()
    projects = [create_test_project(name=name) for name in ("参加A", "参加B", "所属外")]
    members = [
        assign_project_role(user=user, project=p, role_key="viewer")
        for p in projects[:2]
    ]
    authorize_as(client, user)

    def seed(project, token="横断キーワード"):
        """実モデルの安定ID・所属を使って各種類を作成する。"""
        reqdoc = RequirementDocument(
            project_id=project.id,
            title="要件定義書",
            document_code=f"RD-{uuid4().hex}",
            purpose=token,
        )
        task = Task(
            project_id=project.id,
            task_code=f"TASK-{uuid4().hex}",
            title="タスク",
            description=token,
        )
        design = Design(
            project_id=project.id,
            name="テスト設計",
            description=token,
            created_by=user.id,
            updated_by=user.id,
        )
        document = ProjectDocument(
            project_id=project.id,
            title="ドキュメント",
            body=token,
            created_by=user.id,
            updated_by=user.id,
        )
        db.add_all([reqdoc, task, design, document])
        db.flush()
        requirement = Requirement(
            document_id=reqdoc.id,
            requirement_code="REQ-1",
            requirement_type="functional",
            title="個別要件",
            acceptance_criteria=token,
        )
        item = Item(
            design_id=design.id, code="ITEM-1", content="テスト項目", steps=token
        )
        case = Case(
            design_id=design.id,
            source={"item": {"code": "CASE-1", "content": "ケース"}},
            source_hash="a" * 64,
            actual_result=token,
        )
        db.add_all([requirement, item, case])
        db.commit()
        return dict(
            requirement_document=reqdoc,
            requirement=requirement,
            task=task,
            test_design=design,
            test_item=item,
            test_case=case,
            document=document,
        )

    return dict(user=user, projects=projects, members=members, seed=seed)


def search(client, **params):
    """成功レスポンスを取得する。"""
    response = client.get("/search", params={"q": "横断キーワード", **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_all_kinds_body_counts_and_project_filters(client, context):
    """本文・受入条件・実行結果も対象にし、所属外の存在を件数へ含めない。"""
    own = context["seed"](context["projects"][0])
    context["seed"](context["projects"][2])
    result = search(client)
    assert result["total"] == 7
    assert result["counts"] == dict(requirement=2, task=1, test=3, document=1)
    assert {r["kind"] for r in result["items"]} == set(own)
    assert all("横断キーワード" in r["excerpt"] for r in result["items"])
    assert all(r["project_name"] == "参加A" for r in result["items"])
    req = next(r for r in result["items"] if r["kind"] == "requirement")
    assert req["container_id"] == own["requirement_document"].id
    assert search(client, category="test")["total"] == 3
    assert search(client, category="test")["counts"] == result["counts"]
    for project in context["projects"][1:]:
        assert search(client, project_id=project.id)["total"] == 0
    assert search(client, project_id=999999)["counts"] == dict(
        requirement=0, task=0, test=0, document=0
    )


def test_literal_wildcards_case_insensitive_rank_and_bounded_excerpt(
    client, db, context
):
    """SQL特殊文字を文字として探し、長い本文でも短い一致箇所だけ返す。"""
    rows = context["seed"](
        context["projects"][0], token="a" * 3000 + "Literal%_\\Search" + "z" * 3000
    )
    for q in ("%", "_", "\\", "literal%_\\search"):
        result = search(client, q=q)
        assert result["total"] == 7
        assert all(len(r["excerpt"]) <= 240 for r in result["items"])
        assert all("Literal%_\\Search" in r["excerpt"] for r in result["items"])
    rows["task"].title = "Literal%_\\Search"
    db.commit()
    assert search(client, q="  literal%_\\search  ")["items"][0]["kind"] == "task"
    assert search(client, q="' OR 1=1 --")["total"] == 0


@pytest.mark.parametrize(
    "permission,expected",
    [
        ("requirement:read", 2),
        ("task:read", 1),
        ("test_plan:read", 2),
        ("test_case:read", 1),
        ("document:read", 1),
        (None, 0),
    ],
)
def test_permissions_are_applied_before_results_and_counts(
    client, db, context, permission, expected
):
    """Project閲覧だけでは本文を返さず、種類ごとの既存権限を守る。"""
    context["seed"](context["projects"][0])
    role = Role(key="search_limited", name="限定閲覧", scope="project")
    db.add(role)
    db.flush()
    codes = ["project:read"] + ([permission] if permission else [])
    for row in db.query(Permission).filter(Permission.code.in_(codes)).all():
        db.add(RolePermission(role_id=role.id, permission_id=row.id))
    context["members"][0].role_id = role.id
    db.commit()
    result = search(client)
    assert result["total"] == expected
    assert sum(result["counts"].values()) == expected
    projects = client.get(
        "/search/projects", params={"selected_id": context["projects"][0].id}
    ).json()
    assert projects["total"] == (2 if permission else 1)
    assert bool(projects["selected"]) == bool(permission)


def test_removed_membership_deleted_parents_and_spacer_are_excluded(
    client, db, context
):
    """親の削除で子の結果・件数も消え、区切り行は検索しない。"""
    rows = context["seed"](context["projects"][0])
    rows["requirement_document"].deleted_at = datetime.now(UTC)
    rows["test_design"].deleted_at = datetime.now(UTC)
    rows["document"].deleted_at = datetime.now(UTC)
    db.commit()
    assert search(client)["total"] == 1
    rows["test_design"].deleted_at = None
    rows["test_item"].is_spacer = True
    rows["task"].deleted_at = datetime.now(UTC)
    db.commit()
    assert search(client)["total"] == 2
    context["members"][0].deleted_at = datetime.now(UTC)
    db.commit()
    assert search(client)["total"] == 0
    assert client.get("/search/projects").json()["total"] == 1


def test_system_admin_does_not_bypass_project_membership(client, db, context):
    """組織Owner・system_adminでも業務検索は参加案件の権限で制限する。"""
    repo = RbacRepository()
    role = repo.get_role_by_key_scope(db, key="system_admin", scope="system")
    assert role is not None
    repo.assign_role_to_user(db, user=context["user"], role=role)
    context["seed"](context["projects"][2])
    db.commit()
    assert search(client)["total"] == 0
    assert (
        client.get(
            "/search/projects", params={"selected_id": context["projects"][2].id}
        ).json()["selected"]
        is None
    )


def test_tenant_selection_limits_shared_identity(client, db, context):
    """両組織に所属する同じIdentityでも選択中の組織だけを検索する。"""
    context["seed"](context["projects"][0])
    tenant = Tenant(name="別組織", slug="other")
    db.add(tenant)
    db.flush()
    member_role = db.query(Role).filter_by(key="tenant_member", scope="tenant").one()
    project_role = db.query(Role).filter_by(key="viewer", scope="project").one()
    db.add(
        TenantMember(
            tenant_id=tenant.id,
            user_id=context["user"].id,
            role_id=member_role.id,
            status="active",
        )
    )
    project = Project(tenant_id=tenant.id, name="別組織案件", project_code="OTHER")
    db.add(project)
    db.flush()
    db.add(
        ProjectMember(
            project_id=project.id, user_id=context["user"].id, role_id=project_role.id
        )
    )
    db.commit()
    context["seed"](project)
    client.headers["X-Tenant-ID"] = str(tenant.id)
    result = search(client)
    assert result["total"] == 7
    assert {r["project_id"] for r in result["items"]} == {project.id}
    assert client.get("/search/projects").json()["items"][0]["name"] == "別組織案件"
    client.headers["X-Tenant-ID"] = str(context["projects"][0].tenant_id)
    assert {r["project_id"] for r in search(client)["items"]} == {
        context["projects"][0].id
    }


def test_stable_pagination_and_fixed_query_count(client, db, context):
    """案件数が増えてもクエリ数を増やさず、ページに重複・欠落を作らない。"""
    for project in context["projects"][:2]:
        context["seed"](project)
    statements = []

    def record(connection, cursor, statement, parameters, event_context, many):
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        first = search(client, page_size=5)
        first_count = len(statements)
        statements.clear()
        second = search(client, page=2, page_size=5)
        assert len(statements) == first_count
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)
    third = search(client, page=3, page_size=5)
    ids = [
        (r["kind"], r["id"]) for page in (first, second, third) for r in page["items"]
    ]
    assert len(ids) == len(set(ids)) == 14
    assert first["total"] == second["total"] == 14
    assert first["items"] == search(client, page_size=5)["items"]
    candidates = client.get(
        "/search/projects", params={"q": "参加B", "page_size": 1}
    ).json()
    assert candidates["total"] == 1 and candidates["items"][0]["name"] == "参加B"


def test_authentication_and_input_bounds(client, create_test_user):
    """未認証、空語、過大な語・ページ・取得数を拒否する。"""
    assert client.get("/search", params={"q": "検索"}).status_code == 401
    assert client.get("/search/projects").status_code == 401
    authorize_as(client, create_test_user())
    for params in (
        {"q": ""},
        {"q": "  "},
        {"q": "a" * 201},
        {"page_size": 51},
        {"page": 0},
        {"page": 501},
        {"project_id": 0},
        {"category": "users"},
    ):
        assert client.get("/search", params={"q": "検索", **params}).status_code == 422
    assert search(client)["items"] == []


def test_project_read_is_required_and_deleted_projects_disappear(client, db, context):
    """リソース権限だけではProject名も漏らさず、案件削除も全種類へ反映する。"""
    context["seed"](context["projects"][0])
    role = Role(key="search_without_project", name="案件権限なし", scope="project")
    db.add(role)
    db.flush()
    permission = db.query(Permission).filter_by(code="document:read").one()
    db.add(RolePermission(role_id=role.id, permission_id=permission.id))
    context["members"][0].role_id = role.id
    db.commit()
    assert search(client)["total"] == 0
    candidates = client.get(
        "/search/projects", params={"selected_id": context["projects"][0].id}
    ).json()
    assert candidates["selected"] is None
    context["members"][0].role_id = (
        db.query(Role).filter_by(key="viewer", scope="project").one().id
    )
    context["projects"][0].deleted_at = datetime.now(UTC)
    db.commit()
    assert search(client)["counts"] == dict(requirement=0, task=0, test=0, document=0)


def test_more_projects_do_not_add_queries(
    client, db, context, create_test_project, assign_project_role
):
    """検索・集計のクエリ数は参加案件数によらず一定にする。"""
    context["seed"](context["projects"][0])
    statements = []

    def record(connection, cursor, statement, parameters, event_context, many):
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        search(client)
        initial = len(statements)
        for index in range(12):
            project = create_test_project(name=f"追加案件{index}")
            assign_project_role(
                user=context["user"], project=project, role_key="viewer"
            )
            db.add(
                Task(project_id=project.id, task_code="TASK-1", title="横断キーワード")
            )
        db.commit()
        statements.clear()
        assert search(client)["total"] == 19
        assert len(statements) == initial
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)


def test_demo_search_is_private_and_closes_on_expiry(
    client, db, monkeypatch, demo_settings
):
    """デモ利用者ごとの検索境界と期限切れ時の拒否を実Cookieで確認する。"""
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "frontend_public_url", "http://testserver")

    def start_demo(visitor):
        visitor.get("/demo/csrf")
        visitor.headers.update(
            {
                "Origin": "http://testserver",
                "X-CSRF-Token": visitor.cookies["csrf_token"],
            }
        )
        response = visitor.post("/demo/start")
        assert response.status_code == 201, response.text
        visitor.headers["X-CSRF-Token"] = visitor.cookies["csrf_token"]
        return response.json()

    a = start_demo(client)
    with TestClient(client.app) as other:
        b = start_demo(other)
        project = db.query(Project).filter_by(tenant_id=a["tenant_id"]).one()
        response = client.post(
            f"/projects/{project.id}/tasks", json={"title": "横断デモ限定"}
        )
        assert response.status_code == 201, response.text
        assert search(client, q="横断デモ限定")["total"] == 1
        assert search(other, q="横断デモ限定")["total"] == 0
        assert (
            other.get("/search/projects", params={"selected_id": project.id}).json()[
                "selected"
            ]
            is None
        )
        assert a["tenant_id"] != b["tenant_id"]
    demo = db.get(DemoSession, UUID(a["id"]))
    assert demo is not None
    demo.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert client.get("/search", params={"q": "横断デモ限定"}).status_code == 401
    assert client.get("/search/projects").status_code == 401
