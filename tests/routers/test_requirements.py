"""要件定義APIのテスト。"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.project import Project, ProjectMember
from app.models.requirement import (
    Requirement,
    RequirementApproval,
    RequirementChangeLog,
    RequirementComment,
    RequirementDetail,
    RequirementDocument,
    RequirementLink,
    RequirementOpenIssue,
    RequirementRelation,
    RequirementReview,
    RequirementRevision,
    RequirementSection,
    RequirementTargetComment,
)
from app.models.user import User
from tests.fakes.storage import FakeStorageService
from tests.helpers.auth import authorize_as


def test_read_requirement_document_returns_assignee_users(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
    monkeypatch,
) -> None:
    """要件定義書詳細が担当者の軽量ユーザー情報を返すことを確認する。"""
    from app.routers import requirements_shared

    monkeypatch.setattr(requirements_shared, "storage_service", FakeStorageService())
    viewer = create_test_user(email="viewer@example.com")
    author = create_test_user(email="author@example.com", name="Author")
    reviewer = create_test_user(email="reviewer@example.com", name="Reviewer")
    approver = create_test_user(email="approver@example.com", name="Approver")
    author.avatar_key = "users/author.png"
    reviewer.avatar_key = None
    approver.avatar_key = "users/approver.png"
    project = create_test_project(name="Project")
    assign_project_role(user=viewer, project=project, role_key="viewer")
    document = create_test_requirement_document(project=project)
    document.author_id = author.id
    document.reviewer_id = reviewer.id
    document.approver_id = approver.id
    db.commit()
    authorize_as(client, viewer)

    response = client.get(
        f"/projects/{project.id}/requirement-documents/{document.id}",
    )

    assert response.status_code == 200
    assert response.json()["author"] == {
        "id": author.id,
        "email": "author@example.com",
        "name": "Author",
        "avatar_url": "https://example.com/users/author.png?signature=test",
        "is_active": True,
    }
    assert response.json()["reviewer"] == {
        "id": reviewer.id,
        "email": "reviewer@example.com",
        "name": "Reviewer",
        "avatar_url": None,
        "is_active": True,
    }
    assert response.json()["approver"] == {
        "id": approver.id,
        "email": "approver@example.com",
        "name": "Approver",
        "avatar_url": "https://example.com/users/approver.png?signature=test",
        "is_active": True,
    }


def test_list_requirement_documents_returns_assignee_users(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
    monkeypatch,
) -> None:
    """要件定義書一覧が担当者の軽量ユーザー情報を返すことを確認する。"""
    from app.routers import requirements_shared

    monkeypatch.setattr(requirements_shared, "storage_service", FakeStorageService())
    viewer = create_test_user(email="viewer@example.com")
    author = create_test_user(email="author@example.com", name="Author")
    project = create_test_project(name="Project")
    assign_project_role(user=viewer, project=project, role_key="viewer")
    document = create_test_requirement_document(project=project)
    document.author_id = author.id
    db.commit()
    authorize_as(client, viewer)

    response = client.get(f"/projects/{project.id}/requirement-documents")

    assert response.status_code == 200
    assert response.json()["items"][0]["author"] == {
        "id": author.id,
        "email": "author@example.com",
        "name": "Author",
        "avatar_url": "https://example.com/default-avatar.png?signature=test",
        "is_active": True,
    }
    assert response.json()["items"][0]["reviewer"] is None
    assert response.json()["items"][0]["approver"] is None


def test_create_requirement_document_allows_project_member(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """memberが要件定義書を作成できることを確認する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(project_code="REQ", name="Requirement Project")
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/requirement-documents",
        json={
            "title": "Requirements",
            "document_code": "RD-001",
            "purpose": "Manage requirements",
        },
    )

    assert response.status_code == 201
    assert response.json()["project_id"] == project.id
    assert response.json()["title"] == "Requirements"
    assert response.json()["document_code"] == "RD-001"
    assert response.json()["version"] == 1
    assert response.json()["created_by"] == user.id
    assert response.json()["updated_by"] == user.id


def test_create_requirement_document_rejects_viewer(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """viewerの要件定義書作成を拒否する。"""
    user = create_test_user(email="viewer@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=user, project=project, role_key="viewer")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/requirement-documents",
        json={"title": "Requirements", "document_code": "RD-001"},
    )

    assert response.status_code == 403


def test_create_requirement_document_rejects_duplicate_document_code(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
) -> None:
    """重複document_codeでの要件定義書作成を409で拒否する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=user, project=project, role_key="member")
    create_test_requirement_document(project=project, document_code="RD-DUP")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/requirement-documents",
        json={"title": "Duplicate", "document_code": "RD-DUP"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "message": "Requirement document code already exists",
        "code": "DUPLICATE_RESOURCE",
    }


def test_create_requirement_document_rejects_deleted_duplicate_document_code(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """論理削除済み行と衝突するdocument_codeを409で拒否する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=user, project=project, role_key="member")
    deleted_document = create_test_requirement_document(
        project=project,
        document_code="RD-DELETED",
    )
    deleted_document.deleted_at = datetime.now(UTC)
    db.commit()
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/requirement-documents",
        json={"title": "Duplicate", "document_code": "RD-DELETED"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "message": "Requirement document code already exists",
        "code": "DUPLICATE_RESOURCE",
    }


def test_list_requirement_documents_allows_viewer(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
) -> None:
    """viewerが要件定義書一覧を取得できることを確認する。"""
    user = create_test_user(email="viewer@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=user, project=project, role_key="viewer")
    create_test_requirement_document(project=project, title="A", document_code="RD-A")
    create_test_requirement_document(project=project, title="B", document_code="RD-B")
    authorize_as(client, user)

    response = client.get(f"/projects/{project.id}/requirement-documents?page_size=1")

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert response.json()["page_size"] == 1
    assert response.json()["items"][0]["document_code"] == "RD-A"


def test_export_requirement_document_returns_markdown_and_records_change_log(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement_section: Callable[..., RequirementSection],
    create_test_requirement_target_comment: Callable[..., RequirementTargetComment],
    db: Session,
) -> None:
    """要件定義書をMarkdown出力でき、変更履歴が記録されることを確認する。"""
    user = create_test_user(email="viewer@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(
        project=project,
        title="Syncnesto 要件定義書",
        document_code="RD-EXPORT",
    )
    first_section = create_test_requirement_section(
        document=document,
        title="業務要件",
        content="業務要件の本文",
        sort_order=10,
    )
    second_section = create_test_requirement_section(
        document=document,
        title="機能要件",
        content="機能要件の本文",
        sort_order=20,
    )
    db.add_all(
        [
            Requirement(
                document_id=document.id,
                section_id=first_section.id,
                requirement_code="REQ-001",
                requirement_type="business",
                title="業務フローを管理できる",
                description="業務フローを登録・参照できる。",
            ),
            Requirement(
                document_id=document.id,
                section_id=second_section.id,
                requirement_code="REQ-002",
                requirement_type="functional",
                title="ログインできる",
                description="登録済みユーザーがログインできる。",
            ),
            Requirement(
                document_id=document.id,
                requirement_code="REQ-999",
                requirement_type="functional",
                title="未分類要件",
            ),
            RequirementOpenIssue(
                document_id=document.id,
                issue_code="ISSUE-001",
                title="SSO範囲が未確定",
                status="open",
            ),
        ]
    )
    db.commit()
    create_test_requirement_target_comment(
        document=document,
        author=user,
        target_type="document",
        target_id=document.id,
        body="レビューコメント",
    )
    assign_project_role(user=user, project=project, role_key="viewer")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/requirement-documents/{document.id}/exports",
        json={"format": "markdown", "include_comments": True},
    )

    assert response.status_code == 200
    assert response.json()["format"] == "markdown"
    content = response.json()["content"]
    assert "# Syncnesto 要件定義書" in content
    assert "#### REQ-001 業務フローを管理できる" in content
    assert "#### REQ-002 ログインできる" in content
    assert "## 未分類要件" in content
    assert "#### REQ-999 未分類要件" in content
    assert "### ISSUE-001 SSO範囲が未確定" in content
    assert "レビューコメント" in content
    assert content.index("### 業務要件") < content.index("### 機能要件")

    change_log = db.query(RequirementChangeLog).one()
    assert change_log.document_id == document.id
    assert change_log.target_type == "document"
    assert change_log.target_id == document.id
    assert change_log.action == "exported"
    assert change_log.changed_by == user.id
    assert change_log.new_value == {
        "format": "markdown",
        "include_comments": True,
        "include_change_logs": False,
    }


def test_export_requirement_document_rejects_unsupported_format(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
) -> None:
    """未対応の要件定義書出力形式を400で拒否する。"""
    user = create_test_user(email="viewer@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="viewer")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/requirement-documents/{document.id}/exports",
        json={"format": "pdf"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "message": "Unsupported requirement export format",
        "code": "BAD_REQUEST",
    }


def test_export_requirement_document_returns_html(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
) -> None:
    """要件定義書をHTML出力できることを確認する。"""
    user = create_test_user(email="viewer@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(
        project=project,
        title="HTML Export",
        document_code="RD-HTML",
    )
    assign_project_role(user=user, project=project, role_key="viewer")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/requirement-documents/{document.id}/exports",
        json={"format": "html"},
    )

    assert response.status_code == 200
    assert response.json()["format"] == "html"
    assert "<!doctype html>" in response.json()["content"]
    assert "<h1>HTML Export</h1>" in response.json()["content"]
    assert "<li>文書コード: RD-HTML</li>" in response.json()["content"]


def test_update_requirement_document_rejects_stale_version(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """古いversionでの要件定義書更新を409で拒否し、最新情報を返す。"""
    user = create_test_user(email="manager@example.com")
    project = create_test_project(name="Project")
    assign_project_role(user=user, project=project, role_key="manager")
    document = create_test_requirement_document(project=project, title="Current")
    document.title = "Latest"
    document.version = 2
    db.commit()
    db.refresh(document)
    authorize_as(client, user)

    response = client.patch(
        f"/projects/{project.id}/requirement-documents/{document.id}",
        json={"title": "Stale", "version": 1},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_CONFLICT"
    assert response.json()["current"]["id"] == document.id
    assert response.json()["current"]["title"] == "Latest"
    assert response.json()["current"]["version"] == 2


def test_create_requirement_section_allows_member(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
) -> None:
    """memberが要件定義セクションを作成できることを確認する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/requirement-documents/{document.id}/sections",
        json={
            "title": "業務要件",
            "section_type": "business",
            "content": "業務要件を記載する。",
            "sort_order": 10,
        },
    )

    assert response.status_code == 201
    assert response.json()["document_id"] == document.id
    assert response.json()["title"] == "業務要件"
    assert response.json()["section_type"] == "business"
    assert response.json()["content"] == "業務要件を記載する。"
    assert response.json()["sort_order"] == 10
    assert response.json()["version"] == 1
    assert response.json()["created_by"] == user.id


def test_list_requirement_sections_orders_by_sort_order(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement_section: Callable[..., RequirementSection],
) -> None:
    """要件定義セクション一覧が表示順で返ることを確認する。"""
    user = create_test_user(email="viewer@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="viewer")
    create_test_requirement_section(document=document, title="B", sort_order=20)
    create_test_requirement_section(document=document, title="A", sort_order=10)
    authorize_as(client, user)

    response = client.get(
        f"/projects/{project.id}/requirement-documents/{document.id}/sections"
    )

    assert response.status_code == 200
    assert [item["title"] for item in response.json()] == ["A", "B"]


def test_update_requirement_section_rejects_stale_version(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement_section: Callable[..., RequirementSection],
    db: Session,
) -> None:
    """古いversionでのセクション更新を409で拒否し、最新情報を返す。"""
    user = create_test_user(email="manager@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    section = create_test_requirement_section(document=document, title="Current")
    section.title = "Latest"
    section.version = 2
    db.commit()
    db.refresh(section)
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    response = client.patch(
        f"/projects/{project.id}/requirement-sections/{section.id}",
        json={"title": "Stale", "version": 1},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_CONFLICT"
    assert response.json()["current"]["id"] == section.id
    assert response.json()["current"]["title"] == "Latest"
    assert response.json()["current"]["version"] == 2


def test_update_requirement_section_sort_order(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement_section: Callable[..., RequirementSection],
) -> None:
    """セクション表示順をまとめて更新できることを確認する。"""
    user = create_test_user(email="manager@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    section_a = create_test_requirement_section(
        document=document,
        title="A",
        sort_order=10,
    )
    section_b = create_test_requirement_section(
        document=document,
        title="B",
        sort_order=20,
    )
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    response = client.patch(
        f"/projects/{project.id}/requirement-documents/{document.id}"
        "/sections/sort-order",
        json={
            "items": [
                {
                    "section_id": section_a.id,
                    "sort_order": 20,
                    "version": section_a.version,
                },
                {
                    "section_id": section_b.id,
                    "sort_order": 10,
                    "version": section_b.version,
                },
            ],
        },
    )

    assert response.status_code == 200
    assert [item["title"] for item in response.json()] == ["B", "A"]
    assert [item["version"] for item in response.json()] == [2, 2]


def test_delete_requirement_section_soft_deletes(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement_section: Callable[..., RequirementSection],
    db: Session,
) -> None:
    """要件定義セクション削除が論理削除で行われることを確認する。"""
    user = create_test_user(email="manager@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    section = create_test_requirement_section(document=document)
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    response = client.delete(
        f"/projects/{project.id}/requirement-sections/{section.id}"
    )

    assert response.status_code == 204
    db.refresh(section)
    assert section.deleted_at is not None


def test_create_open_issue_allows_member_and_records_change_log(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """memberが未決事項を作成でき、変更履歴が記録されることを確認する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/open-issues",
        json={
            "document_id": document.id,
            "issue_code": "ISSUE-001",
            "title": "ログイン要件の範囲が未確定",
            "description": "SSOを含めるか確認する。",
            "impact_scope": "認証機能",
            "assignee_id": user.id,
            "due_date": "2026-06-30",
        },
    )

    assert response.status_code == 201
    assert response.json()["document_id"] == document.id
    assert response.json()["issue_code"] == "ISSUE-001"
    assert response.json()["status"] == "open"
    assert response.json()["created_by"] == user.id

    change_log = db.query(RequirementChangeLog).one()
    assert change_log.document_id == document.id
    assert change_log.target_type == "open_issue"
    assert change_log.action == "created"
    assert change_log.changed_by == user.id
    assert change_log.new_value["issue_code"] == "ISSUE-001"


def test_create_open_issue_rejects_duplicate_issue_code(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement_open_issue: Callable[..., RequirementOpenIssue],
) -> None:
    """重複issue_codeでの未決事項作成を409で拒否する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    create_test_requirement_open_issue(document=document, issue_code="ISSUE-DUP")
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/open-issues",
        json={
            "document_id": document.id,
            "issue_code": "ISSUE-DUP",
            "title": "Duplicate",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "message": "Requirement open issue code already exists",
        "code": "DUPLICATE_RESOURCE",
    }


def test_list_open_issues_filters_by_document_status_assignee_and_dates(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """未決事項一覧が複数条件で絞り込みできることを確認する。"""
    user = create_test_user(email="viewer@example.com")
    assignee = create_test_user(email="assignee@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    other_document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="viewer")
    related_requirement = Requirement(
        document_id=document.id,
        requirement_code="REQ-RELATED",
        requirement_type="functional",
        title="Related",
    )
    other_related_requirement = Requirement(
        document_id=document.id,
        requirement_code="REQ-OTHER-RELATED",
        requirement_type="functional",
        title="Other Related",
    )
    db.add_all([related_requirement, other_related_requirement])
    db.flush()
    db.add_all(
        [
            RequirementOpenIssue(
                document_id=document.id,
                issue_code="ISSUE-A",
                title="A",
                status="open",
                assignee_id=assignee.id,
                due_date=datetime(2026, 6, 20, tzinfo=UTC).date(),
                related_requirement_id=related_requirement.id,
            ),
            RequirementOpenIssue(
                document_id=document.id,
                issue_code="ISSUE-B",
                title="B",
                status="closed",
                assignee_id=assignee.id,
                due_date=datetime(2026, 6, 25, tzinfo=UTC).date(),
                related_requirement_id=related_requirement.id,
            ),
            RequirementOpenIssue(
                document_id=other_document.id,
                issue_code="ISSUE-C",
                title="C",
                status="open",
                assignee_id=assignee.id,
                due_date=datetime(2026, 6, 20, tzinfo=UTC).date(),
                related_requirement_id=related_requirement.id,
            ),
            RequirementOpenIssue(
                document_id=document.id,
                issue_code="ISSUE-D",
                title="D",
                status="open",
                assignee_id=assignee.id,
                due_date=datetime(2026, 7, 1, tzinfo=UTC).date(),
                related_requirement_id=other_related_requirement.id,
            ),
        ]
    )
    db.commit()
    authorize_as(client, user)

    response = client.get(
        f"/projects/{project.id}/open-issues"
        f"?document_id={document.id}&status=open&assignee_id={assignee.id}"
        "&due_date_from=2026-06-01&due_date_to=2026-06-30"
        f"&related_requirement_id={related_requirement.id}"
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["issue_code"] == "ISSUE-A"


def test_update_open_issue_rejects_stale_version(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement_open_issue: Callable[..., RequirementOpenIssue],
    db: Session,
) -> None:
    """古いversionでの未決事項更新を409で拒否し、最新情報を返す。"""
    user = create_test_user(email="manager@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    issue = create_test_requirement_open_issue(document=document, title="Current")
    issue.title = "Latest"
    issue.version = 2
    db.commit()
    db.refresh(issue)
    assign_project_role(user=user, project=project, role_key="manager")
    authorize_as(client, user)

    response = client.patch(
        f"/projects/{project.id}/open-issues/{issue.id}",
        json={"title": "Stale", "version": 1},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_CONFLICT"
    assert response.json()["current"]["id"] == issue.id
    assert response.json()["current"]["title"] == "Latest"
    assert response.json()["current"]["version"] == 2


def test_promote_open_issue_to_requirement(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement_section: Callable[..., RequirementSection],
    create_test_requirement_open_issue: Callable[..., RequirementOpenIssue],
    db: Session,
) -> None:
    """未決事項を要件へ昇格できることを確認する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    section = create_test_requirement_section(document=document)
    issue = create_test_requirement_open_issue(
        document=document,
        issue_code="ISSUE-001",
        title="SSO対応範囲",
    )
    issue.description = "SSOを初期リリースに含めるか未決。"
    issue.assignee_id = user.id
    db.commit()
    db.refresh(issue)
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/open-issues/{issue.id}/promote-to-requirement",
        json={
            "version": issue.version,
            "requirement_code": "REQ-PROMOTED",
            "requirement_type": "functional",
            "section_id": section.id,
            "priority": "must",
            "resolution": "初期リリースに含める。",
            "reason": "方針決定",
        },
    )

    assert response.status_code == 201
    assert response.json()["document_id"] == document.id
    assert response.json()["section_id"] == section.id
    assert response.json()["requirement_code"] == "REQ-PROMOTED"
    assert response.json()["title"] == "SSO対応範囲"
    assert response.json()["owner_id"] == user.id

    db.refresh(issue)
    assert issue.status == "resolved"
    assert issue.related_requirement_id == response.json()["id"]
    assert issue.resolution == "初期リリースに含める。"

    promoted_log = (
        db.query(RequirementChangeLog)
        .filter(RequirementChangeLog.action == "promoted_to_requirement")
        .one()
    )
    assert promoted_log.target_type == "open_issue"
    assert promoted_log.target_id == issue.id
    assert promoted_log.new_value["requirement_code"] == "REQ-PROMOTED"


def test_list_requirement_change_logs_allows_viewer(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """viewerが要件定義変更履歴を取得できることを確認する。"""
    user = create_test_user(email="viewer@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    other_project = create_test_project(name="Other")
    other_document = create_test_requirement_document(project=other_project)
    assign_project_role(user=user, project=project, role_key="viewer")
    db.add_all(
        [
            RequirementChangeLog(
                document_id=document.id,
                target_type="open_issue",
                target_id=1,
                action="created",
                changed_by=user.id,
            ),
            RequirementChangeLog(
                document_id=other_document.id,
                target_type="open_issue",
                target_id=2,
                action="created",
                changed_by=user.id,
            ),
        ]
    )
    db.commit()
    authorize_as(client, user)

    response = client.get(
        f"/projects/{project.id}/change-logs?target_type=open_issue"
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["document_id"] == document.id
    assert response.json()["items"][0]["target_type"] == "open_issue"


def test_list_requirement_change_logs_filters_by_actor_and_changed_at(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """変更履歴一覧がchanged_by/changed_at範囲で絞り込みできることを確認する。"""
    viewer = create_test_user(email="viewer@example.com")
    actor = create_test_user(email="actor@example.com")
    other_actor = create_test_user(email="other-actor@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=viewer, project=project, role_key="viewer")
    base_time = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    db.add_all(
        [
            RequirementChangeLog(
                document_id=document.id,
                target_type="requirement_item",
                target_id=1,
                action="updated",
                changed_by=actor.id,
                changed_at=base_time,
            ),
            RequirementChangeLog(
                document_id=document.id,
                target_type="requirement_item",
                target_id=2,
                action="updated",
                changed_by=actor.id,
                changed_at=base_time - timedelta(days=2),
            ),
            RequirementChangeLog(
                document_id=document.id,
                target_type="requirement_item",
                target_id=3,
                action="updated",
                changed_by=other_actor.id,
                changed_at=base_time,
            ),
        ]
    )
    db.commit()
    authorize_as(client, viewer)

    response = client.get(
        f"/projects/{project.id}/change-logs"
        f"?changed_by={actor.id}"
        "&changed_at_from=2026-05-31T00:00:00Z"
        "&changed_at_to=2026-06-02T00:00:00Z"
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["target_id"] == 1


def test_request_and_approve_requirement_approval_records_change_logs(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """承認申請と承認ができ、変更履歴が記録されることを確認する。"""
    requester = create_test_user(email="requester@example.com")
    approver = create_test_user(email="approver@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=requester, project=project, role_key="manager")
    assign_project_role(user=approver, project=project, role_key="manager")
    authorize_as(client, requester)

    request_response = client.post(
        f"/projects/{project.id}/approvals/request",
        json={
            "target_type": "document",
            "target_id": document.id,
            "approver_id": approver.id,
            "comment": "承認をお願いします。",
        },
    )

    assert request_response.status_code == 201
    approval_id = request_response.json()["id"]
    assert request_response.json()["document_id"] == document.id
    assert request_response.json()["status"] == "requested"
    assert request_response.json()["requested_by"] == requester.id
    assert request_response.json()["approver_id"] == approver.id

    authorize_as(client, approver)
    approve_response = client.post(
        f"/projects/{project.id}/approvals/{approval_id}/approve",
        json={"comment": "承認します。"},
    )

    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "approved"
    assert approve_response.json()["approved_at"] is not None
    assert approve_response.json()["comment"] == "承認します。"

    actions = [
        row[0]
        for row in db.query(RequirementChangeLog.action)
        .order_by(RequirementChangeLog.id)
        .all()
    ]
    assert actions == ["approval.requested", "approval.approved"]


def test_list_requirement_approvals_filters_and_rejects_decided_retry(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """承認一覧の絞り込みと判断済み承認の再判断拒否を確認する。"""
    user = create_test_user(email="manager@example.com")
    approver = create_test_user(email="approver@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    other_document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="manager")
    db.add_all(
        [
            RequirementApproval(
                document_id=document.id,
                target_type="document",
                target_id=document.id,
                status="rejected",
                approver_id=approver.id,
                requested_by=user.id,
            ),
            RequirementApproval(
                document_id=other_document.id,
                target_type="document",
                target_id=other_document.id,
                status="requested",
                approver_id=approver.id,
                requested_by=user.id,
            ),
        ]
    )
    db.commit()
    rejected_approval = (
        db.query(RequirementApproval)
        .filter(RequirementApproval.document_id == document.id)
        .one()
    )
    authorize_as(client, user)

    list_response = client.get(
        f"/projects/{project.id}/approvals"
        f"?target_type=document&target_id={document.id}&status=rejected"
        f"&approver_id={approver.id}"
    )
    retry_response = client.post(
        f"/projects/{project.id}/approvals/{rejected_approval.id}/approve",
        json={"comment": "再承認"},
    )

    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1
    assert list_response.json()["items"][0]["id"] == rejected_approval.id
    assert retry_response.status_code == 400
    assert retry_response.json() == {
        "message": "Requirement approval already decided",
        "code": "BAD_REQUEST",
    }


def test_create_target_comment_for_section_records_change_log(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement_section: Callable[..., RequirementSection],
    db: Session,
) -> None:
    """セクションへコメントでき、変更履歴が記録されることを確認する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    section = create_test_requirement_section(document=document)
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/comments",
        json={
            "target_type": "section",
            "target_id": section.id,
            "body": "この章の説明を補足してください。",
        },
    )

    assert response.status_code == 201
    assert response.json()["document_id"] == document.id
    assert response.json()["target_type"] == "section"
    assert response.json()["target_id"] == section.id
    assert response.json()["body"] == "この章の説明を補足してください。"
    assert response.json()["author_id"] == user.id
    assert response.json()["is_resolved"] is False
    assert response.json()["version"] == 1

    change_log = (
        db.query(RequirementChangeLog)
        .filter(RequirementChangeLog.action == "comment.created")
        .one()
    )
    assert change_log.document_id == document.id
    assert change_log.target_type == "comment"
    assert change_log.target_id == response.json()["id"]
    assert change_log.new_value["body"] == "この章の説明を補足してください。"


def test_create_target_comment_rejects_viewer(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
) -> None:
    """viewerの汎用コメント作成を拒否する。"""
    user = create_test_user(email="viewer@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="viewer")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/comments",
        json={
            "target_type": "document",
            "target_id": document.id,
            "body": "コメント",
        },
    )

    assert response.status_code == 403


def test_create_target_comment_rejects_target_from_other_project(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
) -> None:
    """別プロジェクトの対象へのコメント作成を404で拒否する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(project_code="A", name="Project A")
    other_project = create_test_project(project_code="B", name="Project B")
    other_document = create_test_requirement_document(project=other_project)
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/comments",
        json={
            "target_type": "document",
            "target_id": other_document.id,
            "body": "コメント",
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "message": "Requirement comment target not found",
        "code": "NOT_FOUND",
    }


def test_list_target_comments_allows_viewer(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement_target_comment: Callable[..., RequirementTargetComment],
) -> None:
    """viewerが対象コメント一覧を取得できることを確認する。"""
    viewer = create_test_user(email="viewer@example.com")
    author = create_test_user(email="author@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=viewer, project=project, role_key="viewer")
    create_test_requirement_target_comment(
        document=document,
        author=author,
        target_type="document",
        target_id=document.id,
        body="First",
    )
    create_test_requirement_target_comment(
        document=document,
        author=author,
        target_type="document",
        target_id=document.id,
        body="Second",
    )
    authorize_as(client, viewer)

    response = client.get(
        f"/projects/{project.id}/comments"
        f"?target_type=document&target_id={document.id}"
    )

    assert response.status_code == 200
    assert [item["body"] for item in response.json()] == ["First", "Second"]


def test_update_target_comment_rejects_stale_version(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement_target_comment: Callable[..., RequirementTargetComment],
    db: Session,
) -> None:
    """古いversionでのコメント更新を409で拒否する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    comment = create_test_requirement_target_comment(
        document=document,
        author=user,
        target_type="document",
        target_id=document.id,
        body="Current",
    )
    comment.body = "Latest"
    comment.version = 2
    db.commit()
    db.refresh(comment)
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    response = client.patch(
        f"/projects/{project.id}/comments/{comment.id}",
        json={"body": "Stale", "version": 1},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "VERSION_CONFLICT"
    assert response.json()["current"]["id"] == comment.id
    assert response.json()["current"]["body"] == "Latest"
    assert response.json()["current"]["version"] == 2


def test_resolve_and_reopen_target_comment(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement_target_comment: Callable[..., RequirementTargetComment],
    db: Session,
) -> None:
    """コメントの解決と再オープンができることを確認する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    comment = create_test_requirement_target_comment(
        document=document,
        author=user,
        target_type="document",
        target_id=document.id,
    )
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    resolve_response = client.post(
        f"/projects/{project.id}/comments/{comment.id}/resolve",
        json={"version": comment.version, "reason": "対応済み"},
    )
    reopen_response = client.post(
        f"/projects/{project.id}/comments/{comment.id}/reopen",
        json={"version": resolve_response.json()["version"], "reason": "再確認"},
    )

    assert resolve_response.status_code == 200
    assert resolve_response.json()["is_resolved"] is True
    assert reopen_response.status_code == 200
    assert reopen_response.json()["is_resolved"] is False
    assert reopen_response.json()["version"] == 3

    actions = [
        row[0]
        for row in db.query(RequirementChangeLog.action)
        .order_by(RequirementChangeLog.id)
        .all()
    ]
    assert actions == ["comment.resolved", "comment.reopened"]


def test_delete_target_comment_soft_deletes(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement_target_comment: Callable[..., RequirementTargetComment],
    db: Session,
) -> None:
    """コメント削除が論理削除で行われることを確認する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    comment = create_test_requirement_target_comment(
        document=document,
        author=user,
        target_type="document",
        target_id=document.id,
    )
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    response = client.delete(f"/projects/{project.id}/comments/{comment.id}")

    assert response.status_code == 204
    db.refresh(comment)
    assert comment.deleted_at is not None

    change_log = (
        db.query(RequirementChangeLog)
        .filter(RequirementChangeLog.action == "comment.deleted")
        .one()
    )
    assert change_log.target_id == comment.id


def test_create_requirement_allows_member(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement_section: Callable[..., RequirementSection],
) -> None:
    """memberが要件を作成できることを確認する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    section = create_test_requirement_section(document=document)
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/requirements",
        json={
            "document_id": document.id,
            "section_id": section.id,
            "requirement_code": "REQ-001",
            "requirement_type": "functional",
            "title": "Login",
            "description": "User can login.",
        },
    )

    assert response.status_code == 201
    assert response.json()["document_id"] == document.id
    assert response.json()["section_id"] == section.id
    assert response.json()["requirement_code"] == "REQ-001"
    assert response.json()["requirement_type"] == "functional"
    assert response.json()["title"] == "Login"
    assert response.json()["version"] == 1
    assert response.json()["created_by"] == user.id


def test_create_requirement_rejects_document_from_other_project(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
) -> None:
    """別プロジェクトのdocument_idを使った要件作成を拒否する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(project_code="A", name="Project A")
    other_project = create_test_project(project_code="B", name="Project B")
    document = create_test_requirement_document(project=other_project)
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/requirements",
        json={
            "document_id": document.id,
            "requirement_code": "REQ-001",
            "requirement_type": "functional",
            "title": "Login",
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "message": "Requirement document not found",
        "code": "NOT_FOUND",
    }


def test_create_requirement_rejects_section_from_other_document(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement_section: Callable[..., RequirementSection],
) -> None:
    """別要件定義書のsection_idを使った要件作成を拒否する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    other_document = create_test_requirement_document(project=project)
    other_section = create_test_requirement_section(document=other_document)
    assign_project_role(user=user, project=project, role_key="member")
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/requirements",
        json={
            "document_id": document.id,
            "section_id": other_section.id,
            "requirement_code": "REQ-001",
            "requirement_type": "functional",
            "title": "Login",
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "message": "Requirement section not found",
        "code": "NOT_FOUND",
    }


def test_create_requirement_rejects_duplicate_requirement_code(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """重複requirement_codeでの要件作成を409で拒否する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="member")
    db.add(
        Requirement(
            document_id=document.id,
            requirement_code="REQ-DUP",
            requirement_type="functional",
            title="Existing",
        )
    )
    db.commit()
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/requirements",
        json={
            "document_id": document.id,
            "requirement_code": "REQ-DUP",
            "requirement_type": "functional",
            "title": "Duplicate",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "message": "Requirement code already exists",
        "code": "DUPLICATE_RESOURCE",
    }


def test_list_requirements_filters_by_document_status_priority_and_owner(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """要件一覧が複数条件で絞り込みできることを確認する。"""
    user = create_test_user(email="viewer@example.com")
    owner = create_test_user(email="owner@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    other_document = create_test_requirement_document(project=project)
    section = RequirementSection(
        document_id=document.id,
        title="Section",
        section_type="business",
        sort_order=10,
        status="draft",
    )
    db.add(section)
    db.flush()
    assign_project_role(user=user, project=project, role_key="viewer")
    db.add_all(
        [
            Requirement(
                document_id=document.id,
                section_id=section.id,
                requirement_code="REQ-A",
                requirement_type="functional",
                title="A",
                status="approved",
                priority="must",
                owner_id=owner.id,
            ),
            Requirement(
                document_id=document.id,
                requirement_code="REQ-B",
                requirement_type="business",
                title="B",
                status="draft",
                priority="must",
                owner_id=owner.id,
            ),
            Requirement(
                document_id=other_document.id,
                requirement_code="REQ-C",
                requirement_type="functional",
                title="C",
                status="approved",
                priority="must",
                owner_id=owner.id,
            ),
        ]
    )
    db.commit()
    authorize_as(client, user)

    response = client.get(
        f"/projects/{project.id}/requirements"
        f"?document_id={document.id}&section_id={section.id}&status=approved"
        f"&priority=must&owner_id={owner.id}"
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["requirement_code"] == "REQ-A"


def test_update_requirement_creates_revision(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """要件更新時に改訂履歴が作成されることを確認する。"""
    user = create_test_user(email="manager@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="manager")
    requirement = Requirement(
        document_id=document.id,
        requirement_code="REQ-001",
        requirement_type="functional",
        title="Before",
    )
    db.add(requirement)
    db.commit()
    db.refresh(requirement)
    authorize_as(client, user)

    response = client.patch(
        f"/projects/{project.id}/requirements/{requirement.id}",
        json={
            "title": "After",
            "version": requirement.version,
            "change_summary": "タイトル変更",
            "reason": "表現調整",
        },
    )

    assert response.status_code == 200
    assert response.json()["title"] == "After"
    assert response.json()["version"] == 2

    revisions = db.query(RequirementRevision).all()
    assert len(revisions) == 1
    assert revisions[0].requirement_id == requirement.id
    assert revisions[0].version == 2
    assert revisions[0].changed_by == user.id
    assert revisions[0].change_summary == "タイトル変更"
    assert revisions[0].reason == "表現調整"
    assert revisions[0].before_value["title"] == "Before"
    assert revisions[0].after_value["title"] == "After"


def test_update_requirement_rejects_viewer(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """viewerの要件更新を拒否する。"""
    user = create_test_user(email="viewer@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="viewer")
    requirement = Requirement(
        document_id=document.id,
        requirement_code="REQ-001",
        requirement_type="functional",
        title="Before",
    )
    db.add(requirement)
    db.commit()
    db.refresh(requirement)
    authorize_as(client, user)

    response = client.patch(
        f"/projects/{project.id}/requirements/{requirement.id}",
        json={"title": "After", "version": requirement.version},
    )

    assert response.status_code == 403


def test_list_requirement_revisions_allows_viewer(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """viewerが要件改訂履歴を取得できることを確認する。"""
    user = create_test_user(email="viewer@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="viewer")
    requirement = Requirement(
        document_id=document.id,
        requirement_code="REQ-001",
        requirement_type="functional",
        title="Title",
    )
    db.add(requirement)
    db.flush()
    revision = RequirementRevision(
        requirement_id=requirement.id,
        version=1,
        changed_by=user.id,
        before_value=None,
        after_value={"title": "Title"},
    )
    db.add(revision)
    db.commit()
    authorize_as(client, user)

    response = client.get(
        f"/projects/{project.id}/requirements/{requirement.id}/revisions"
    )

    assert response.status_code == 200
    assert response.json()[0]["requirement_id"] == requirement.id
    assert response.json()[0]["version"] == 1
    assert response.json()[0]["after_value"] == {"title": "Title"}


def test_requirement_detail_crud_allows_manager(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """managerが要件詳細を作成、取得、更新、削除できることを確認する。"""
    user = create_test_user(email="manager@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="manager")
    requirement = Requirement(
        document_id=document.id,
        requirement_code="REQ-001",
        requirement_type="screen",
        title="Screen",
    )
    db.add(requirement)
    db.commit()
    db.refresh(requirement)
    authorize_as(client, user)

    create_response = client.post(
        f"/projects/{project.id}/requirements/{requirement.id}/details",
        json={
            "detail_type": "screen",
            "detail_json": {"screen_name": "ログイン画面", "url_path": "/login"},
        },
    )
    detail_id = create_response.json()["id"]
    list_response = client.get(
        f"/projects/{project.id}/requirements/{requirement.id}/details"
    )
    update_response = client.patch(
        f"/projects/{project.id}/requirements/{requirement.id}/details/{detail_id}",
        json={"detail_json": {"screen_name": "ログイン", "url_path": "/login"}},
    )
    delete_response = client.delete(
        f"/projects/{project.id}/requirements/{requirement.id}/details/{detail_id}"
    )

    assert create_response.status_code == 201
    assert create_response.json()["detail_type"] == "screen"
    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == detail_id
    assert update_response.status_code == 200
    assert update_response.json()["detail_json"]["screen_name"] == "ログイン"
    assert delete_response.status_code == 204
    assert db.get(RequirementDetail, detail_id) is None


def test_create_requirement_detail_rejects_viewer(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """viewerの要件詳細作成を拒否する。"""
    user = create_test_user(email="viewer@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="viewer")
    requirement = Requirement(
        document_id=document.id,
        requirement_code="REQ-001",
        requirement_type="screen",
        title="Screen",
    )
    db.add(requirement)
    db.commit()
    db.refresh(requirement)
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/requirements/{requirement.id}/details",
        json={"detail_type": "screen", "detail_json": {"screen_name": "Login"}},
    )

    assert response.status_code == 403


def test_requirement_link_create_list_delete_allows_member(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """memberが要件リンクを作成、取得、削除できることを確認する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="member")
    requirement = Requirement(
        document_id=document.id,
        requirement_code="REQ-001",
        requirement_type="functional",
        title="Login",
    )
    db.add(requirement)
    db.commit()
    db.refresh(requirement)
    authorize_as(client, user)

    create_response = client.post(
        f"/projects/{project.id}/requirements/{requirement.id}/links",
        json={"linked_type": "api", "linked_id": "POST /auth/login"},
    )
    link_id = create_response.json()["id"]
    list_response = client.get(
        f"/projects/{project.id}/requirements/{requirement.id}/links"
    )
    delete_response = client.delete(
        f"/projects/{project.id}/requirements/{requirement.id}/links/{link_id}"
    )

    assert create_response.status_code == 201
    assert create_response.json()["linked_type"] == "api"
    assert create_response.json()["linked_id"] == "POST /auth/login"
    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == link_id
    assert delete_response.status_code == 204
    assert db.get(RequirementLink, link_id) is None


def test_requirement_relation_create_list_delete_allows_member(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """memberが要件関連を作成、取得、削除できることを確認する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="member")
    source_requirement = Requirement(
        document_id=document.id,
        requirement_code="REQ-SOURCE",
        requirement_type="functional",
        title="Source",
    )
    target_requirement = Requirement(
        document_id=document.id,
        requirement_code="REQ-TARGET",
        requirement_type="functional",
        title="Target",
    )
    db.add_all([source_requirement, target_requirement])
    db.commit()
    db.refresh(source_requirement)
    db.refresh(target_requirement)
    authorize_as(client, user)

    create_response = client.post(
        f"/projects/{project.id}/requirements/{source_requirement.id}/relations",
        json={
            "target_type": "requirement_item",
            "target_id": str(target_requirement.id),
            "relation_type": "depends_on",
            "description": "対象要件に依存する。",
        },
    )
    relation_id = create_response.json()["id"]
    list_response = client.get(
        f"/projects/{project.id}/requirements/{source_requirement.id}/relations"
    )
    delete_response = client.delete(
        f"/projects/{project.id}/requirements/{source_requirement.id}"
        f"/relations/{relation_id}"
    )

    assert create_response.status_code == 201
    assert create_response.json()["document_id"] == document.id
    assert create_response.json()["source_requirement_id"] == source_requirement.id
    assert create_response.json()["target_id"] == str(target_requirement.id)
    assert create_response.json()["relation_type"] == "depends_on"
    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == relation_id
    assert delete_response.status_code == 204
    assert db.get(RequirementRelation, relation_id) is None

    actions = [
        row[0]
        for row in db.query(RequirementChangeLog.action)
        .order_by(RequirementChangeLog.id)
        .all()
    ]
    assert actions == ["created", "deleted"]


def test_requirement_comment_create_list_delete_allows_member(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """memberが要件コメントを作成、取得、削除できることを確認する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="member")
    requirement = Requirement(
        document_id=document.id,
        requirement_code="REQ-001",
        requirement_type="functional",
        title="Login",
    )
    db.add(requirement)
    db.commit()
    db.refresh(requirement)
    authorize_as(client, user)

    create_response = client.post(
        f"/projects/{project.id}/requirements/{requirement.id}/comments",
        json={"comment": "確認してください"},
    )
    comment_id = create_response.json()["id"]
    list_response = client.get(
        f"/projects/{project.id}/requirements/{requirement.id}/comments"
    )
    delete_response = client.delete(
        f"/projects/{project.id}/requirements/{requirement.id}/comments/{comment_id}"
    )

    assert create_response.status_code == 201
    assert create_response.json()["user_id"] == user.id
    assert create_response.json()["comment"] == "確認してください"
    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == comment_id
    assert delete_response.status_code == 204
    assert db.get(RequirementComment, comment_id) is None


def test_requirement_review_crud_allows_manager(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """managerが要件レビューを作成、取得、更新、削除できることを確認する。"""
    user = create_test_user(email="manager@example.com")
    reviewer = create_test_user(email="reviewer@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="manager")
    requirement = Requirement(
        document_id=document.id,
        requirement_code="REQ-001",
        requirement_type="functional",
        title="Login",
    )
    db.add(requirement)
    db.commit()
    db.refresh(requirement)
    authorize_as(client, user)

    create_response = client.post(
        f"/projects/{project.id}/requirements/{requirement.id}/reviews",
        json={
            "reviewer_id": reviewer.id,
            "status": "pending",
            "comment": "レビューお願いします",
        },
    )
    review_id = create_response.json()["id"]
    list_response = client.get(
        f"/projects/{project.id}/requirements/{requirement.id}/reviews"
    )
    update_response = client.patch(
        f"/projects/{project.id}/requirements/{requirement.id}/reviews/{review_id}",
        json={"status": "approved", "comment": "OK"},
    )
    delete_response = client.delete(
        f"/projects/{project.id}/requirements/{requirement.id}/reviews/{review_id}"
    )

    assert create_response.status_code == 201
    assert create_response.json()["reviewer_id"] == reviewer.id
    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == review_id
    assert update_response.status_code == 200
    assert update_response.json()["status"] == "approved"
    assert update_response.json()["comment"] == "OK"
    assert delete_response.status_code == 204
    assert db.get(RequirementReview, review_id) is None


def test_create_requirement_review_rejects_member(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """memberの要件レビュー作成を拒否する。"""
    user = create_test_user(email="member@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="member")
    requirement = Requirement(
        document_id=document.id,
        requirement_code="REQ-001",
        requirement_type="functional",
        title="Login",
    )
    db.add(requirement)
    db.commit()
    db.refresh(requirement)
    authorize_as(client, user)

    response = client.post(
        f"/projects/{project.id}/requirements/{requirement.id}/reviews",
        json={"reviewer_id": user.id, "status": "pending"},
    )

    assert response.status_code == 403


def test_read_requirement_summary_returns_related_resources_with_latest_limits(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    db: Session,
) -> None:
    """要件summaryが関連情報をまとめて返し、コメントと履歴を直近20件に絞ることを確認する。"""
    user = create_test_user(email="viewer@example.com")
    reviewer = create_test_user(email="reviewer@example.com")
    project = create_test_project(name="Project")
    document = create_test_requirement_document(project=project)
    assign_project_role(user=user, project=project, role_key="viewer")
    requirement = Requirement(
        document_id=document.id,
        requirement_code="REQ-001",
        requirement_type="functional",
        title="Login",
    )
    db.add(requirement)
    db.flush()
    db.add(
        RequirementDetail(
            requirement_id=requirement.id,
            detail_type="screen",
            detail_json={"screen_name": "ログイン画面"},
        )
    )
    db.add(
        RequirementLink(
            requirement_id=requirement.id,
            linked_type="api",
            linked_id="POST /auth/login",
        )
    )
    db.add(
        RequirementReview(
            requirement_id=requirement.id,
            reviewer_id=reviewer.id,
            status="pending",
        )
    )
    for index in range(21):
        db.add(
            RequirementComment(
                requirement_id=requirement.id,
                user_id=user.id,
                comment=f"comment-{index}",
            )
        )
        db.add(
            RequirementRevision(
                requirement_id=requirement.id,
                version=index + 1,
                changed_by=user.id,
                before_value=None,
                after_value={"title": f"Title {index}"},
            )
        )
    db.commit()
    authorize_as(client, user)

    response = client.get(
        f"/projects/{project.id}/requirements/{requirement.id}/summary"
    )

    assert response.status_code == 200
    assert response.json()["requirement"]["id"] == requirement.id
    assert response.json()["details"][0]["detail_json"] == {
        "screen_name": "ログイン画面"
    }
    assert response.json()["links"][0]["linked_id"] == "POST /auth/login"
    assert response.json()["reviews"][0]["reviewer_id"] == reviewer.id
    assert len(response.json()["comments"]) == 20
    assert response.json()["comments"][0]["comment"] == "comment-1"
    assert response.json()["comments"][-1]["comment"] == "comment-20"
    assert len(response.json()["revisions"]) == 20
    assert response.json()["revisions"][0]["version"] == 2
    assert response.json()["revisions"][-1]["version"] == 21
