"""プロジェクトAPIのテスト。"""

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token
from app.models.project import Project, ProjectMember
from app.models.user import User
from app.repositories.rbac import RbacRepository


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
        json={"name": "Project A", "description": "Description A"},
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": 1,
        "name": "Project A",
        "description": "Description A",
    }


def test_create_project_requires_project_create_permission(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """project:create権限なしの作成を拒否する。"""
    normal_user = create_test_user(email="normal@example.com")
    authorize_as(client, normal_user)

    response = client.post("/projects", json={"name": "Project A"})

    assert response.status_code == 403


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
    create_test_project(name="Project A")
    create_test_project(name="Project B")
    authorize_as(client, admin_user)

    response = client.get("/projects")

    assert response.status_code == 200
    assert [project["name"] for project in response.json()] == [
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
    joined_project = create_test_project(name="Joined")
    create_test_project(name="Not Joined")
    assign_project_role(user=user, project=joined_project, role_key="viewer")
    authorize_as(client, user)

    response = client.get("/projects")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": joined_project.id,
            "name": "Joined",
            "description": "Project Description",
        }
    ]


def test_read_project_allows_project_viewer(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """viewerが所属プロジェクトを取得できることを確認する。"""
    user = create_test_user(email="viewer@example.com")
    project = create_test_project(name="Readable")
    assign_project_role(user=user, project=project, role_key="viewer")
    authorize_as(client, user)

    response = client.get(f"/projects/{project.id}")

    assert response.status_code == 200
    assert response.json()["name"] == "Readable"


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

    response = client.patch(f"/projects/{project.id}", json={"name": "After"})

    assert response.status_code == 200
    assert response.json()["name"] == "After"


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

    response = client.patch(f"/projects/{project.id}", json={"name": "After"})

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


def test_add_project_member_allows_project_admin(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    db: Session,
) -> None:
    """project_adminがメンバーを追加できることを確認する。"""
    admin_user = create_test_user(email="project-admin@example.com")
    target_user = create_test_user(email="target@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=admin_user, project=project, role_key="project_admin")
    authorize_as(client, admin_user)
    role_id = get_project_role_id(db, "member")

    response = client.post(
        f"/projects/{project.id}/members",
        json={"user_id": target_user.id, "role_id": role_id},
    )

    assert response.status_code == 201
    assert response.json()["user_id"] == target_user.id
    assert response.json()["role_id"] == role_id


def test_add_project_member_rejects_viewer(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    db: Session,
) -> None:
    """viewerのメンバー追加を拒否する。"""
    viewer = create_test_user(email="viewer@example.com")
    target_user = create_test_user(email="target@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=viewer, project=project, role_key="viewer")
    authorize_as(client, viewer)
    role_id = get_project_role_id(db, "member")

    response = client.post(
        f"/projects/{project.id}/members",
        json={"user_id": target_user.id, "role_id": role_id},
    )

    assert response.status_code == 403


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
            "role_id": member.role_id,
        }
    ]


def test_update_project_member_allows_project_admin(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    db: Session,
) -> None:
    """project_adminがメンバーロールを変更できることを確認。"""
    admin_user = create_test_user(email="admin@example.com")
    target_user = create_test_user(email="target@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=admin_user, project=project, role_key="project_admin")
    member = assign_project_role(user=target_user, project=project, role_key="viewer")
    authorize_as(client, admin_user)
    role_id = get_project_role_id(db, "member")

    response = client.patch(
        f"/projects/{project.id}/members/{target_user.id}",
        json={"role_id": role_id},
    )

    assert response.status_code == 200
    assert response.json()["id"] == member.id
    assert response.json()["role_id"] == role_id


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
    authorize_as(client, admin_user)

    response = client.delete(f"/projects/{project.id}/members/{target_user.id}")

    assert response.status_code == 204
    db.refresh(member)
    assert member.deleted_at is not None
