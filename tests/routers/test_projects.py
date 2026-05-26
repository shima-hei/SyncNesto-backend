"""プロジェクトAPIのテスト。"""

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token
from app.models.project import Project, ProjectMember
from app.models.user import User
from app.repositories.rbac import RbacRepository


class FakeStorageService:
    """テスト用StorageService。"""

    def generate_presigned_url(self, avatar_key: str | None) -> str | None:
        """固定の署名付きURLを返す。"""
        if avatar_key is None:
            return None

        return f"https://example.com/{avatar_key}?signature=test"


def authorize_as(client: TestClient, user: User) -> None:
    """TestClientを指定ユーザーとして認証済みにする。"""
    access_token = create_access_token(subject=user.email)
    client.cookies.set(settings.auth_cookie_name, access_token)


def get_project_role_id(db: Session, role_key: str) -> int:
    """プロジェクトロールIDを取得する。"""
    role = RbacRepository().get_role_by_key_scope(
        db,
        key=role_key,
        scope="project",
    )
    assert role is not None
    return role.id


def test_create_project_returns_project_for_system_admin(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """system_adminがプロジェクトを作成できることを確認する。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    authorize_as(client, admin_user)

    response = client.post(
        "/projects",
        json={
            "project_code": "PRJ-A",
            "name": "Project A",
            "description": "Description A",
            "status": "active",
        },
    )

    assert response.status_code == 201
    assert response.json()["project_code"] == "PRJ-A"
    assert response.json()["name"] == "Project A"
    assert response.json()["description"] == "Description A"
    assert response.json()["status"] == "active"
    assert response.json()["version"] == 1
    assert response.json()["created_by"] == admin_user.id
    assert response.json()["updated_by"] == admin_user.id


def test_create_project_requires_project_create_permission(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """project:create権限なしの作成を拒否する。"""
    normal_user = create_test_user(email="normal@example.com")
    authorize_as(client, normal_user)

    response = client.post("/projects", json={"name": "Project A"})

    assert response.status_code == 403


def test_create_project_rejects_duplicate_project_code(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
) -> None:
    """重複project_codeでのプロジェクト作成を拒否する。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    create_test_project(project_code="DUP", name="Existing")
    authorize_as(client, admin_user)

    response = client.post(
        "/projects",
        json={
            "project_code": "DUP",
            "name": "Duplicate",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "message": "Project code already exists",
        "code": "DUPLICATE_RESOURCE",
    }


def test_list_projects_returns_all_projects_for_system_admin(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
) -> None:
    """system_adminが全プロジェクト一覧を取得できることを確認。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    create_test_project(project_code="PRJ-A", name="Project A")
    create_test_project(project_code="PRJ-B", name="Project B")
    authorize_as(client, admin_user)

    response = client.get("/projects")

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert response.json()["page"] == 1
    assert response.json()["page_size"] == 20
    assert [project["name"] for project in response.json()["items"]] == [
        "Project A",
        "Project B",
    ]


def test_list_projects_returns_joined_projects_for_project_member(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """所属プロジェクトのみ取得できることを確認する。"""
    user = create_test_user(email="member@example.com")
    joined_project = create_test_project(project_code="JOINED", name="Joined")
    create_test_project(project_code="NOT-JOINED", name="Not Joined")
    assign_project_role(user=user, project=joined_project, role_key="viewer")
    authorize_as(client, user)

    response = client.get("/projects")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == joined_project.id
    assert response.json()["items"][0]["project_code"] == "JOINED"
    assert response.json()["items"][0]["name"] == "Joined"
    assert "version" not in response.json()["items"][0]


def test_list_projects_paginates_projects_for_system_admin(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
) -> None:
    """プロジェクト一覧がページングされることを確認する。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    create_test_project(project_code="PRJ-A", name="Project A")
    create_test_project(project_code="PRJ-B", name="Project B")
    authorize_as(client, admin_user)

    response = client.get("/projects?page=2&page_size=1")

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert response.json()["page"] == 2
    assert response.json()["page_size"] == 1
    assert response.json()["items"][0]["project_code"] == "PRJ-B"


def test_list_projects_searches_projects(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
) -> None:
    """プロジェクト一覧がproject_code/name/descriptionで検索できることを確認する。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    create_test_project(
        project_code="SYNC",
        name="Syncnesto",
        description="Backend project",
    )
    create_test_project(
        project_code="OTHER",
        name="Other",
        description="Other project",
    )
    authorize_as(client, admin_user)

    response = client.get("/projects?q=Backend")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["project_code"] == "SYNC"


def test_list_project_member_users_returns_project_members(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    monkeypatch,
) -> None:
    """プロジェクト所属ユーザー検索が所属メンバーのみ返すことを確認する。"""
    from app.routers import projects

    monkeypatch.setattr(projects, "storage_service", FakeStorageService())
    current_user = create_test_user(email="current@example.com")
    target_user = create_test_user(email="target@example.com", name="Target User")
    non_member = create_test_user(email="non-member@example.com", name="Target User")
    project = create_test_project(project_code="MEMBERS", name="Members")
    assign_project_role(user=current_user, project=project, role_key="viewer")
    assign_project_role(user=target_user, project=project, role_key="member")
    authorize_as(client, current_user)

    response = client.get(f"/projects/{project.id}/member-users?q=target")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": target_user.id,
                "email": "target@example.com",
                "name": "Target User",
                "avatar_url": "https://example.com/default-avatar.png?signature=test",
                "is_active": True,
            }
        ]
    }
    assert non_member.id != response.json()["items"][0]["id"]


def test_list_project_member_users_requires_project_read_permission(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
) -> None:
    """プロジェクト未参加ユーザーの所属ユーザー検索を拒否する。"""
    user = create_test_user(email="outsider@example.com")
    project = create_test_project(project_code="MEMBERS", name="Members")
    authorize_as(client, user)

    response = client.get(f"/projects/{project.id}/member-users")

    assert response.status_code == 403


def test_list_projects_filters_by_status(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
) -> None:
    """プロジェクト一覧がstatusで絞り込みできることを確認する。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    create_test_project(project_code="ACTIVE", name="Active", status="active")
    create_test_project(project_code="ARCHIVED", name="Archived", status="archived")
    authorize_as(client, admin_user)

    response = client.get("/projects?status=archived")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["project_code"] == "ARCHIVED"


def test_read_project_allows_project_viewer(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """viewerが所属プロジェクトを取得できることを確認する。"""
    user = create_test_user(email="viewer@example.com")
    project = create_test_project(
        project_code="READ",
        name="Readable",
        status="active",
    )
    assign_project_role(user=user, project=project, role_key="viewer")
    authorize_as(client, user)

    response = client.get(f"/projects/{project.id}")

    assert response.status_code == 200
    assert response.json()["project_code"] == "READ"
    assert response.json()["name"] == "Readable"
    assert response.json()["status"] == "active"
    assert response.json()["version"] == project.version
    assert "created_at" in response.json()
    assert "updated_at" in response.json()


def test_read_project_rejects_non_member(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
) -> None:
    """未所属ユーザーのプロジェクト取得を拒否する。"""
    user = create_test_user(email="outsider@example.com")
    project = create_test_project(name="Secret")
    authorize_as(client, user)

    response = client.get(f"/projects/{project.id}")

    assert response.status_code == 403


def test_read_current_project_role_returns_project_role_for_member(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """プロジェクトメンバーの対象プロジェクト内ロールを返すことを確認する。"""
    user = create_test_user(email="manager@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    response = client.get(f"/projects/{project.id}/me")

    assert response.status_code == 200
    assert response.json() == {
        "project_id": project.id,
        "role": {"key": "manager", "name": "マネージャー"},
        "is_system_admin": False,
    }


def test_read_current_project_role_returns_system_admin_without_project_role(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
) -> None:
    """system_adminが未所属プロジェクトでもis_system_admin=trueで取得できることを確認する。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    project = create_test_project(name="Project")
    authorize_as(client, admin_user)

    response = client.get(f"/projects/{project.id}/me")

    assert response.status_code == 200
    assert response.json() == {
        "project_id": project.id,
        "role": None,
        "is_system_admin": True,
    }


def test_read_current_project_role_returns_both_for_system_admin_member(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """system_adminがプロジェクトロールも持つ場合は両方返すことを確認する。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    project = create_test_project(name="Project")
    assign_project_role(user=admin_user, project=project, role_key="project_admin")
    authorize_as(client, admin_user)

    response = client.get(f"/projects/{project.id}/me")

    assert response.status_code == 200
    assert response.json() == {
        "project_id": project.id,
        "role": {"key": "project_admin", "name": "プロジェクト管理者"},
        "is_system_admin": True,
    }


def test_read_current_project_role_rejects_non_member(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
) -> None:
    """未参加かつsystem_adminでないユーザーの対象プロジェクト内ロール取得を拒否する。"""
    user = create_test_user(email="outsider@example.com")
    project = create_test_project(name="Project")
    authorize_as(client, user)

    response = client.get(f"/projects/{project.id}/me")

    assert response.status_code == 403


def test_read_current_project_role_returns_not_found_for_unknown_project(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """存在しないプロジェクトでは404を返すことを確認する。"""
    admin_user = create_test_user(
        email="admin@example.com",
        system_role="system_admin",
    )
    authorize_as(client, admin_user)

    response = client.get("/projects/999/me")

    assert response.status_code == 404
    assert response.json() == {"message": "Project not found", "code": "NOT_FOUND"}


def test_read_current_project_role_requires_authentication(
    client: TestClient,
    create_test_project: Callable[..., Project],
) -> None:
    """未認証の対象プロジェクト内ロール取得を拒否する。"""
    project = create_test_project(name="Project")

    response = client.get(f"/projects/{project.id}/me")

    assert response.status_code == 401


def test_update_project_allows_project_admin(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """project_adminがプロジェクトを更新できることを確認する。"""
    user = create_test_user(email="admin@example.com")
    project = create_test_project(name="Before")
    assign_project_role(user=user, project=project, role_key="project_admin")
    authorize_as(client, user)

    response = client.patch(
        f"/projects/{project.id}",
        json={
            "project_code": "AFTER",
            "name": "After",
            "description": None,
            "status": "archived",
            "start_date": "2026-05-01",
            "end_date": "2026-05-31",
            "version": project.version,
        },
    )

    assert response.status_code == 200
    assert response.json()["project_code"] == "AFTER"
    assert response.json()["name"] == "After"
    assert response.json()["description"] is None
    assert response.json()["status"] == "archived"
    assert response.json()["start_date"] == "2026-05-01"
    assert response.json()["end_date"] == "2026-05-31"
    assert response.json()["version"] == project.version + 1
    assert response.json()["updated_by"] == user.id


def test_update_project_rejects_stale_version_with_current_project(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    db: Session,
) -> None:
    """古いversionでのプロジェクト更新を409で拒否し、最新情報を返す。"""
    user = create_test_user(email="admin@example.com")
    project = create_test_project(name="Current")
    assign_project_role(user=user, project=project, role_key="project_admin")
    project.name = "Latest"
    project.version = 2
    db.commit()
    db.refresh(project)
    authorize_as(client, user)

    response = client.patch(
        f"/projects/{project.id}",
        json={"name": "Stale Update", "version": 1},
    )

    assert response.status_code == 409
    assert response.json()["message"] == "Resource version conflict"
    assert response.json()["code"] == "VERSION_CONFLICT"
    assert response.json()["current"]["id"] == project.id
    assert response.json()["current"]["project_code"] == project.project_code
    assert response.json()["current"]["name"] == "Latest"
    assert response.json()["current"]["description"] == "Project Description"
    assert response.json()["current"]["version"] == 2


def test_update_project_requires_version(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """プロジェクト更新時にversionが必須であることを確認する。"""
    user = create_test_user(email="admin@example.com")
    project = create_test_project(name="Before")
    assign_project_role(user=user, project=project, role_key="project_admin")
    authorize_as(client, user)

    response = client.patch(f"/projects/{project.id}", json={"name": "After"})

    assert response.status_code == 422


def test_update_project_rejects_viewer(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """viewerのプロジェクト更新を拒否する。"""
    user = create_test_user(email="viewer@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=user, project=project, role_key="viewer")
    authorize_as(client, user)

    response = client.patch(
        f"/projects/{project.id}",
        json={"name": "After", "version": project.version},
    )

    assert response.status_code == 403


def test_delete_project_allows_project_admin(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    db: Session,
) -> None:
    """project_adminがプロジェクトを論理削除できることを確認。"""
    user = create_test_user(email="admin@example.com")
    project = create_test_project(name="Delete")
    assign_project_role(user=user, project=project, role_key="project_admin")
    authorize_as(client, user)

    response = client.delete(f"/projects/{project.id}")

    assert response.status_code == 204
    db.refresh(project)
    assert project.deleted_at is not None
    assert project.updated_by == user.id


def test_add_project_member_allows_project_admin(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """project_adminがメンバーを追加できることを確認する。"""
    admin_user = create_test_user(email="project-admin@example.com")
    target_user = create_test_user(email="target@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=admin_user, project=project, role_key="project_admin")
    authorize_as(client, admin_user)

    response = client.post(
        f"/projects/{project.id}/members",
        json={"user_id": target_user.id, "role_key": "member"},
    )

    assert response.status_code == 201
    assert response.json()["user_id"] == target_user.id
    assert response.json()["role"] == {"key": "member", "name": "メンバー"}


def test_add_project_member_rejects_viewer(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """viewerのメンバー追加を拒否する。"""
    viewer = create_test_user(email="viewer@example.com")
    target_user = create_test_user(email="target@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=viewer, project=project, role_key="viewer")
    authorize_as(client, viewer)

    response = client.post(
        f"/projects/{project.id}/members",
        json={"user_id": target_user.id, "role_key": "member"},
    )

    assert response.status_code == 403


def test_add_project_member_rejects_invalid_role_key(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """存在しないrole_keyでのメンバー追加を拒否する。"""
    admin_user = create_test_user(email="admin@example.com")
    target_user = create_test_user(email="target@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=admin_user, project=project, role_key="project_admin")
    authorize_as(client, admin_user)

    response = client.post(
        f"/projects/{project.id}/members",
        json={"user_id": target_user.id, "role_key": "unknown_role"},
    )

    assert response.status_code == 404
    assert response.json() == {
        "message": "Project role not found",
        "code": "NOT_FOUND",
    }


def test_list_project_members_allows_project_viewer(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """viewerがメンバー一覧を取得できることを確認する。"""
    viewer = create_test_user(email="viewer@example.com")
    project = create_test_project(name="Project")
    member = assign_project_role(user=viewer, project=project, role_key="viewer")
    authorize_as(client, viewer)

    response = client.get(f"/projects/{project.id}/members")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": member.id,
            "project_id": project.id,
            "user_id": viewer.id,
            "role": {"key": "viewer", "name": "閲覧者"},
            "version": 1,
        }
    ]


def test_update_project_member_allows_project_admin(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """project_adminがメンバーロールを変更できることを確認。"""
    admin_user = create_test_user(email="admin@example.com")
    target_user = create_test_user(email="target@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=admin_user, project=project, role_key="project_admin")
    member = assign_project_role(user=target_user, project=project, role_key="viewer")
    authorize_as(client, admin_user)

    response = client.patch(
        f"/projects/{project.id}/members/{target_user.id}",
        json={"role_key": "member", "version": member.version},
    )

    assert response.status_code == 200
    assert response.json()["id"] == member.id
    assert response.json()["role"] == {"key": "member", "name": "メンバー"}
    assert response.json()["version"] == member.version + 1


def test_update_project_member_rejects_stale_version_with_current_member(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    db: Session,
) -> None:
    """古いversionでのメンバー更新を409で拒否し、最新情報を返す。"""
    admin_user = create_test_user(email="admin@example.com")
    target_user = create_test_user(email="target@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=admin_user, project=project, role_key="project_admin")
    member = assign_project_role(user=target_user, project=project, role_key="viewer")
    manager_role_id = get_project_role_id(db, "manager")
    member.role_id = manager_role_id
    member.version = 2
    db.commit()
    db.refresh(member)
    authorize_as(client, admin_user)

    response = client.patch(
        f"/projects/{project.id}/members/{target_user.id}",
        json={"role_key": "member", "version": 1},
    )

    assert response.status_code == 409
    assert response.json() == {
        "message": "Resource version conflict",
        "code": "VERSION_CONFLICT",
        "current": {
            "id": member.id,
            "project_id": project.id,
            "user_id": target_user.id,
            "role": {
                "key": "manager",
                "name": "マネージャー",
            },
            "version": 2,
        },
    }


def test_update_project_member_requires_version(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """メンバー更新時にversionが必須であることを確認する。"""
    admin_user = create_test_user(email="admin@example.com")
    target_user = create_test_user(email="target@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=admin_user, project=project, role_key="project_admin")
    assign_project_role(user=target_user, project=project, role_key="viewer")
    authorize_as(client, admin_user)

    response = client.patch(
        f"/projects/{project.id}/members/{target_user.id}",
        json={"role_key": "member"},
    )

    assert response.status_code == 422


def test_update_project_member_rejects_invalid_role_key(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """存在しないrole_keyでのメンバー更新を拒否する。"""
    admin_user = create_test_user(email="admin@example.com")
    target_user = create_test_user(email="target@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=admin_user, project=project, role_key="project_admin")
    member = assign_project_role(user=target_user, project=project, role_key="viewer")
    authorize_as(client, admin_user)

    response = client.patch(
        f"/projects/{project.id}/members/{target_user.id}",
        json={"role_key": "unknown_role", "version": member.version},
    )

    assert response.status_code == 404
    assert response.json() == {
        "message": "Project role not found",
        "code": "NOT_FOUND",
    }


def test_remove_project_member_allows_project_admin(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    db: Session,
) -> None:
    """project_adminがメンバーを削除できることを確認する。"""
    admin_user = create_test_user(email="admin@example.com")
    target_user = create_test_user(email="target@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=admin_user, project=project, role_key="project_admin")
    member = assign_project_role(user=target_user, project=project, role_key="viewer")
    member_id = member.id
    authorize_as(client, admin_user)

    response = client.delete(f"/projects/{project.id}/members/{target_user.id}")

    assert response.status_code == 204
    db.expire_all()
    assert db.get(ProjectMember, member_id) is None


def test_remove_project_member_allows_readding_same_user(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """物理削除後に同じユーザーを再追加できることを確認する。"""
    admin_user = create_test_user(email="admin@example.com")
    target_user = create_test_user(email="target@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=admin_user, project=project, role_key="project_admin")
    assign_project_role(user=target_user, project=project, role_key="viewer")
    authorize_as(client, admin_user)

    delete_response = client.delete(f"/projects/{project.id}/members/{target_user.id}")
    create_response = client.post(
        f"/projects/{project.id}/members",
        json={"user_id": target_user.id, "role_key": "member"},
    )

    assert delete_response.status_code == 204
    assert create_response.status_code == 201
    assert create_response.json()["role"] == {"key": "member", "name": "メンバー"}
