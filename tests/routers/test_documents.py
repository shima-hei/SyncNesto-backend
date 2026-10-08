"""文書の版、権限、関連先、非公開添付とデモ回収を検証する。"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.document import (
    DocumentAttachment,
    DocumentLink,
    DocumentRevision,
    ProjectDocument,
)
from app.models.project import Project
from app.models.rbac import Permission, Role, RolePermission
from app.routers import documents
from app.services.demo import DemoService
from app.services.storage import StorageService
from tests.fakes.storage import MemoryS3Client
from tests.helpers.auth import authorize_as


@pytest.fixture
def context(
    client, create_test_user, create_test_project, assign_project_role, monkeypatch
):
    """本人と同一Projectの管理者権限、isolated S3 fakeを用意する。"""
    user = create_test_user()
    project = create_test_project()
    assign_project_role(user=user, project=project, role_key="project_admin")
    authorize_as(client, user)
    s3 = MemoryS3Client()
    monkeypatch.setattr(documents.attachments, "storage", StorageService(s3))
    return user, project, f"/projects/{project.id}/documents", s3


def create(client, base, title="作業ガイド", body="初版"):
    """実際の作成APIを通して初期版を生成する。"""
    response = client.post(base, json={"title": title, "body": body})
    assert response.status_code == 201, response.text
    return response.json()


def test_document_revision_search_conflict_and_delete(client, context, db):
    """過去版は保持し、競合で新しい版を追加せず、削除後は取得を閉じる。"""
    _, _, base, _ = context
    doc = create(client, base)
    url = f"{base}/{doc['id']}"
    saved = client.patch(
        url, json={"version": 1, "title": "改訂版", "body": "本文検索用キーワード"}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2
    conflict = client.patch(
        url, json={"version": 1, "title": "古い入力", "body": "失わない"}
    )
    assert conflict.status_code == 409
    assert conflict.json()["current"]["body"] == "本文検索用キーワード"
    assert client.get(url + "/revisions").json()[0]["number"] == 2
    assert client.get(url + "/revisions/1").json()["body"] == "初版"
    assert client.get(base, params={"q": "検索用"}).json()["total"] == 1
    assert "body" not in client.get(base).json()["items"][0]
    assert client.get(base, params={"q": "%"}).json()["total"] == 0
    assert client.delete(url, params={"version": 1}).status_code == 409
    assert client.delete(url, params={"version": 2}).status_code == 204
    assert client.get(url).status_code == 404
    assert client.get(url + "/revisions/1").status_code == 404
    assert db.scalar(select(func.count()).select_from(DocumentRevision)) == 2


@pytest.mark.parametrize(
    "role,can_edit,can_delete",
    [
        ("viewer", False, False),
        ("member", True, False),
        ("manager", True, False),
        ("project_admin", True, True),
    ],
)
def test_document_permissions(
    client, context, create_test_user, assign_project_role, role, can_edit, can_delete
):
    """既存document permissionを閲覧・編集・削除ごとに適用する。"""
    _, project, base, _ = context
    doc = create(client, base)
    user = create_test_user(email="other@example.com")
    assign_project_role(user=user, project=project, role_key=role)
    authorize_as(client, user)
    assert client.get(base).status_code == 200
    assert client.post(base, json={"title": "別の文書"}).status_code == (
        201 if can_edit else 403
    )
    assert client.patch(
        f"{base}/{doc['id']}", json={"version": 1, "title": "変更", "body": ""}
    ).status_code == (200 if can_edit else 403)
    assert client.delete(
        f"{base}/{doc['id']}", params={"version": 2 if can_edit else 1}
    ).status_code == (204 if can_delete else 403)


def test_document_wrong_project_and_link_target(
    client, context, create_test_project, assign_project_role, create_test_task
):
    """同じ組織の別Projectでも文書IDや関連先を流用できない。"""
    user, project, base, _ = context
    other = create_test_project()
    assign_project_role(user=user, project=other, role_key="project_admin")
    doc = create(client, base)
    url = f"{base}/{doc['id']}"
    assert client.get(f"/projects/{other.id}/documents/{doc['id']}").status_code == 404
    foreign = create_test_task(project=other)
    assert (
        client.post(
            url + "/links", json={"target_type": "task", "target_id": foreign.id}
        ).status_code
        == 404
    )
    task = create_test_task(project=project)
    data = {"target_type": "task", "target_id": task.id}
    linked = client.post(url + "/links", json=data)
    assert linked.status_code == 201, linked.text
    assert linked.json()[0]["title"] == task.title
    assert len(client.post(url + "/links", json=data).json()) == 1
    assert (
        client.get(base + "/link-candidates", params={"target_type": "task"}).json()[0][
            "target_id"
        ]
        == task.id
    )


def test_document_links_hide_unreadable_and_deleted_targets(
    client, context, db, create_test_task, create_test_user, assign_project_role
):
    """文書の閲覧権限だけでは関連先の本文・タイトルを漏らさない。"""
    owner, project, base, _ = context
    doc = create(client, base)
    task = create_test_task(project=project, title="機密の関連先")
    url = f"{base}/{doc['id']}/links"
    assert (
        client.post(url, json={"target_type": "task", "target_id": task.id}).status_code
        == 201
    )
    member = create_test_user(email="document-only@example.com")
    assign_project_role(user=member, project=project, role_key="member")
    role_id = db.scalar(
        select(Role.id).where(Role.key == "member", Role.scope == "project")
    )
    permission_id = db.scalar(
        select(Permission.id).where(Permission.code == "task:read")
    )
    grant = db.scalar(
        select(RolePermission).where(
            RolePermission.role_id == role_id,
            RolePermission.permission_id == permission_id,
        )
    )
    assert grant is not None
    db.delete(grant)
    db.commit()
    authorize_as(client, member)
    response = client.get(url)
    assert response.status_code == 200
    assert response.json()[0]["title"] is None
    assert "機密" not in response.text
    assert (
        client.get(
            base + "/link-candidates", params={"target_type": "task"}
        ).status_code
        == 403
    )
    authorize_as(client, owner)
    assert client.delete(f"/tasks/{task.id}").status_code == 204
    assert client.get(url).json()[0]["title"] is None


def test_attachment_direct_completion_and_private_download(
    client, context, monkeypatch
):
    """本人・用途のtoken、確定先、再送と強制downloadヘッダーを確認する。"""
    _, project, base, s3 = context
    monkeypatch.setattr(settings, "file_upload_mode", "presigned")
    doc = create(client, base)
    second = create(client, base)
    url = f"{base}/{doc['id']}/attachments"
    plan = client.post(
        url + "/upload-plan",
        json={"filename": "運用.txt", "content_type": "text/plain", "byte_size": 5},
    )
    assert plan.status_code == 200, plan.text
    pending = s3.presigned_params["Key"]
    s3.put_object(Key=pending, Body=b"hello", ContentType="text/plain")
    token = {"upload_token": plan.json()["upload_token"]}
    assert (
        client.post(
            f"{base}/{second['id']}/attachments/upload-complete", json=token
        ).status_code
        == 400
    )
    complete = client.post(url + "/upload-complete", json=token)
    assert complete.status_code == 201, complete.text
    attachment = complete.json()
    assert "storage_key" not in attachment
    assert pending not in s3.objects
    assert len(s3.objects) == 1
    assert list(s3.objects)[0].startswith(
        f"projects/{project.id}/documents/{doc['id']}/"
    )
    assert (
        client.post(url + "/upload-complete", json=token).json()["id"]
        == attachment["id"]
    )
    download = client.get(url + f"/{attachment['id']}/download")
    assert download.status_code == 200, download.text
    assert s3.presigned_params["ResponseContentDisposition"].startswith("attachment;")
    assert s3.presigned_params["ResponseContentType"] == "application/octet-stream"
    assert client.delete(url + f"/{attachment['id']}").status_code == 204
    assert client.get(url + f"/{attachment['id']}/download").status_code == 404


@pytest.mark.parametrize(
    "name,kind,content",
    [
        ("bad.html", "text/html", b"<script>"),
        ("fake.pdf", "application/pdf", b"fake"),
        ("bad.txt", "text/plain", b"\xff"),
    ],
)
def test_attachment_content_rejected(client, context, name, kind, content):
    """未許可形式・偽装・非UTF8・制御文字を保存前に拒否する。"""
    _, _, base, s3 = context
    doc = create(client, base)
    response = client.post(
        f"{base}/{doc['id']}/attachments", files={"file": (name, content, kind)}
    )
    assert response.status_code == 400, response.text
    assert not s3.objects


def test_attachment_plan_rejects_control_characters(client, context):
    """multipartのエスケープ前のファイル名も計画APIで検証する。"""
    _, _, base, s3 = context
    doc = create(client, base)
    response = client.post(
        f"{base}/{doc['id']}/attachments/upload-plan",
        json={
            "filename": "control\n.txt",
            "content_type": "text/plain",
            "byte_size": 4,
        },
    )
    assert response.status_code == 400, response.text
    assert not s3.objects


def test_attachment_competing_completion_preserves_committed_file(
    client, context, db, monkeypatch
):
    """予約commit後に別の確定が先行しても、その行・ファイルを上書きや削除しない。"""
    user, project, base, s3 = context
    monkeypatch.setattr(settings, "file_upload_mode", "presigned")
    doc = create(client, base)
    url = f"{base}/{doc['id']}/attachments"
    plan = client.post(
        url + "/upload-plan",
        json={
            "filename": "guide.txt",
            "content_type": "text/plain",
            "byte_size": 5,
        },
    ).json()
    pending = s3.presigned_params["Key"]
    identity = UUID(pending.rsplit("/", 1)[-1])
    s3.put_object(Key=pending, Body=b"later", ContentType="text/plain")
    winner_key = f"projects/{project.id}/documents/{doc['id']}/{identity}/{uuid4()}"
    put = s3.put_object

    def interleaved_put(**kwargs):
        put(**kwargs)
        assert kwargs["Key"] != winner_key
        put(Key=winner_key, Body=b"first", ContentType="text/plain")
        db.add(
            DocumentAttachment(
                id=identity,
                document_id=doc["id"],
                filename="guide.txt",
                content_type="text/plain",
                byte_size=5,
                storage_key=winner_key,
                uploaded_by=user.id,
            )
        )
        db.commit()

    monkeypatch.setattr(s3, "put_object", interleaved_put)
    result = documents.attachments.complete(
        db, project.id, doc["id"], plan["upload_token"], user.id
    )
    assert result.id == identity
    assert list(s3.objects) == [winner_key]
    assert s3.objects[winner_key][0] == b"first"
    stored = db.get(DocumentAttachment, identity)
    assert stored is not None and stored.storage_key == winner_key


def test_demo_document_children_are_recovered(client, db, monkeypatch):
    """サンプルと追加文書、版、関連、添付もデモ終了で物理回収する。"""
    monkeypatch.setattr(settings, "app_env", "demo")
    monkeypatch.setattr(settings, "frontend_public_url", "http://testserver")
    monkeypatch.setattr(settings, "file_upload_mode", "server")
    client.get("/demo/csrf")
    client.headers.update(
        {"Origin": "http://testserver", "X-CSRF-Token": client.cookies["csrf_token"]}
    )
    started = client.post("/demo/start")
    assert started.status_code == 201, started.text
    client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
    project = db.scalar(
        select(Project).where(Project.tenant_id == started.json()["tenant_id"])
    )
    assert project is not None
    base = f"/projects/{project.id}/documents"
    assert client.get(base).json()["total"] == 1
    doc = create(client, base)
    url = f"{base}/{doc['id']}"
    target = client.get(
        base + "/link-candidates", params={"target_type": "task"}
    ).json()[0]
    assert (
        client.post(
            url + "/links",
            json={"target_type": "task", "target_id": target["target_id"]},
        ).status_code
        == 201
    )
    s3 = MemoryS3Client()
    monkeypatch.setattr(documents.attachments, "storage", StorageService(s3))
    uploaded = client.post(
        url + "/attachments", files={"file": ("guide.txt", b"hello", "text/plain")}
    )
    assert uploaded.status_code == 201, uploaded.text
    assert list(s3.objects)[0].startswith("demo/")
    demo_id = UUID(started.json()["id"])
    DemoService().revoke(db, demo_id, "test")
    DemoService().cleanup(demo_id, StorageService(s3))
    for model in (ProjectDocument, DocumentRevision, DocumentAttachment, DocumentLink):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert not s3.objects
