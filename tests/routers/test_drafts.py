"""下書き保存APIのテスト。"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.draft import Draft
from app.models.project import Project, ProjectMember
from app.models.user import User
from app.services.draft import DraftService
from tests.helpers.auth import authorize_as


def test_upsert_and_list_drafts(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """下書きを作成・更新・一覧取得できることを確認する。"""
    user = create_test_user(email="draft@example.com")
    authorize_as(client, user)

    create_response = client.put(
        "/drafts/requirements:items:create:1",
        json={
            "resource_type": "requirement",
            "project_id": None,
            "schema_version": 1,
            "content": {"title": "ログイン要件"},
        },
    )

    assert create_response.status_code == 200
    body = create_response.json()
    assert body["owner_user_id"] == user.id
    assert body["scope_key"] == "requirements:items:create:1"
    assert body["content"] == {"title": "ログイン要件"}
    assert body["version"] == 1

    update_response = client.put(
        "/drafts/requirements:items:create:1",
        json={
            "resource_type": "requirement",
            "schema_version": 1,
            "content": {"title": "ログイン要件2"},
            "version": body["version"],
        },
    )

    assert update_response.status_code == 200
    assert update_response.json()["content"] == {"title": "ログイン要件2"}
    assert update_response.json()["version"] == 2

    list_response = client.get("/drafts?resource_type=requirement")

    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1
    assert list_response.json()["items"][0]["id"] == body["id"]


def test_upsert_draft_rejects_stale_version(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """古いversionでの下書き更新を409で拒否する。"""
    user = create_test_user(email="draft-conflict@example.com")
    authorize_as(client, user)
    client.put(
        "/drafts/tasks:create:1",
        json={
            "resource_type": "task",
            "schema_version": 1,
            "content": {"title": "Task"},
        },
    )

    response = client.put(
        "/drafts/tasks:create:1",
        json={
            "resource_type": "task",
            "schema_version": 1,
            "content": {"title": "Stale"},
            "version": 999,
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_CONFLICT"
    assert response.json()["current"]["version"] == 1


def test_read_draft_hides_other_user_draft(
    client: TestClient,
    create_test_user: Callable[..., User],
) -> None:
    """他ユーザーの下書きは取得できないことを確認する。"""
    owner = create_test_user(email="draft-owner@example.com")
    other = create_test_user(email="draft-other@example.com")
    authorize_as(client, owner)
    create_response = client.put(
        "/drafts/requirements:documents:create:1",
        json={
            "resource_type": "requirement_document",
            "schema_version": 1,
            "content": {"title": "Doc"},
        },
    )

    authorize_as(client, other)
    response = client.get(f"/drafts/{create_response.json()['id']}")

    assert response.status_code == 404


def test_upsert_draft_requires_project_read_permission(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """project_id付き下書きはプロジェクト閲覧権限を要求する。"""
    member = create_test_user(email="draft-member@example.com")
    outsider = create_test_user(email="draft-outsider@example.com")
    project = create_test_project(name="Draft Project")
    assign_project_role(user=member, project=project, role_key="viewer")

    authorize_as(client, member)
    allowed_response = client.put(
        "/drafts/requirements:documents:create:1",
        json={
            "resource_type": "requirement_document",
            "project_id": project.id,
            "schema_version": 1,
            "content": {"title": "Doc"},
        },
    )

    assert allowed_response.status_code == 200

    authorize_as(client, outsider)
    denied_response = client.put(
        "/drafts/requirements:documents:create:1",
        json={
            "resource_type": "requirement_document",
            "project_id": project.id,
            "schema_version": 1,
            "content": {"title": "Doc"},
        },
    )

    assert denied_response.status_code == 403


def test_delete_expired_drafts_deletes_only_expired_rows(
    create_test_user: Callable[..., User],
    db: Session,
) -> None:
    """期限切れ下書きだけを削除する。"""
    user = create_test_user(email="draft-expired@example.com")
    now = datetime.now(UTC)
    expired = Draft(
        owner_user_id=user.id,
        scope_key="expired",
        resource_type="requirement",
        schema_version=1,
        content={"title": "expired"},
        expires_at=now - timedelta(seconds=1),
    )
    active = Draft(
        owner_user_id=user.id,
        scope_key="active",
        resource_type="requirement",
        schema_version=1,
        content={"title": "active"},
        expires_at=now + timedelta(days=1),
    )
    db.add_all([expired, active])
    db.commit()

    deleted_count = DraftService().delete_expired_drafts(db, now=now)

    assert deleted_count == 1
    assert db.query(Draft).filter(Draft.scope_key == "expired").first() is None
    assert db.query(Draft).filter(Draft.scope_key == "active").first() is not None
