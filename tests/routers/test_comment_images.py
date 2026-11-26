"""コメントの権限・添付の確定と取消を実際のAPIで確認する。"""

from base64 import b64decode
from uuid import uuid4

import pytest

from app.core.config import settings
from app.routers import comment_images
from app.services.comment_image import CommentImageService
from app.services.storage import StorageService
from tests.fakes.storage import MemoryS3Client
from tests.helpers.auth import authorize_as

PNG = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a/1sAAAAASUVORK5CYII="
)


@pytest.fixture
def image_storage(monkeypatch):
    """バイナリ保存の振る舞いを検証できるS3 fake。"""
    s3 = MemoryS3Client()
    monkeypatch.setattr(
        comment_images, "service", CommentImageService(StorageService(s3))
    )
    monkeypatch.setattr(settings, "file_upload_mode", "server")
    return s3


@pytest.fixture(
    params=["task", "document", "requirement", "api", "screen", "test_design"]
)
def context(
    request,
    client,
    create_test_user,
    create_test_project,
    assign_project_role,
    create_test_requirement_document,
    create_test_requirement,
    create_test_task,
):
    """現在利用するコメント投稿経路を通常の権限で準備する。"""
    user = create_test_user(email="media-author@example.com")
    viewer = create_test_user(email="media-viewer@example.com")
    outsider = create_test_user(email="media-outsider@example.com")
    project = create_test_project()
    assign_project_role(user=user, project=project, role_key="project_admin")
    assign_project_role(user=viewer, project=project, role_key="viewer")
    authorize_as(client, user)
    project_url = f"/projects/{project.id}"
    surface = request.param
    kind = surface
    if surface == "task":
        task = create_test_task(project=project)
        url, target = f"/tasks/{task.id}/comments", {}
    elif surface in {"document", "requirement"}:
        kind = "requirement"
        document = create_test_requirement_document(project=project)
        target_type, target_id = "document", document.id
        if surface == "requirement":
            target_type = "requirement_item"
            target_id = create_test_requirement(document=document).id
        url, target = (
            f"{project_url}/comments",
            {"target_type": target_type, "target_id": target_id},
        )
    elif surface in {"api", "screen"}:
        kind = "specification"
        field = "path" if surface == "api" else "route"
        response = client.post(
            f"{project_url}/specifications",
            json={
                "title": "添付を確認する設計",
                "content": {"type": surface, field: "/sample"},
            },
        )
        assert response.status_code == 201, response.text
        url = f"{project_url}/specifications/{response.json()['id']}/comments"
        target = {"specification_version": 1, "field_path": f"content/{field}"}
    else:
        response = client.post(
            f"{project_url}/test-designs", json={"name": "添付を確認するテスト"}
        )
        assert response.status_code == 201, response.text
        url = f"{project_url}/test-designs/{response.json()['id']}/comments"
        target = {"target_type": "design"}
    return user, viewer, outsider, f"{project_url}/comment-images", kind, url, target


def test_comment_images_attach_and_follow_comment_permissions(
    client, context, image_storage
):
    user, viewer, outsider, root, kind, url, target = context
    file = {"file": ("sample.png", PNG, "image/png")}
    draft = client.post(f"{root}/{kind}/upload", files=file)
    assert draft.status_code == 201, draft.text
    image = draft.json()
    path = f"{root}/{image['id']}"
    assert client.get(path).status_code == 404
    authorize_as(client, viewer)
    assert client.post(f"{root}/{kind}/upload", files=file).status_code == 403
    authorize_as(client, user)
    extra = client.post(f"{root}/{kind}/upload", files=file).json()
    assert client.delete(f"{root}/{extra['id']}").status_code == 204
    assert len(image_storage.objects) == 1

    payload = {**target, "body": "画像を確認してください https://example.com/trace"}
    assert (
        client.post(
            url, json={**payload, "image_ids": [image["id"], str(uuid4())]}
        ).status_code
        == 400
    )
    assert (
        client.get(url, params=target if kind == "requirement" else None).json() == []
    )
    assert client.get(path).status_code == 404

    created = client.post(url, json={**payload, "image_ids": [image["id"]]})
    assert created.status_code == 201, created.text
    assert created.json()["images"] == [image]
    assert (
        client.post(url, json={**payload, "image_ids": [image["id"]]}).status_code
        == 400
    )
    assert client.delete(path).status_code == 404
    assert client.get(url, params=target if kind == "requirement" else None).json()[0][
        "images"
    ] == [image]

    authorize_as(client, outsider)
    assert client.get(path).status_code in {403, 404}
    authorize_as(client, viewer)
    opened = client.get(path)
    assert opened.status_code == 200, opened.text
    assert opened.headers["cache-control"] == "private, no-store"
    assert opened.headers["content-type"] == "image/png"
    assert opened.content == PNG

    if kind == "task":
        authorize_as(client, user)
        assert (
            client.delete(f"/task-comments/{created.json()['id']}").status_code == 204
        )
        assert client.get(path).status_code == 404


def test_presigned_comment_image_is_scope_bound_and_retryable(
    client,
    create_test_user,
    create_test_project,
    assign_project_role,
    image_storage,
    monkeypatch,
):
    """一時PUTは用途固定、実バイトを検証し、同一完了の再送は同じ画像を返す。"""
    monkeypatch.setattr(settings, "file_upload_mode", "presigned")
    user = create_test_user()
    project = create_test_project()
    assign_project_role(user=user, project=project, role_key="project_admin")
    authorize_as(client, user)
    base = f"/projects/{project.id}/comment-images"
    response = client.post(
        base + "/task/upload-plan",
        json={
            "filename": "proof.png",
            "content_type": "image/png",
            "byte_size": len(PNG),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "presigned"
    upload_key = image_storage.presigned_params["Key"]
    image_storage.objects[upload_key] = (PNG, "image/png")
    token = {"upload_token": response.json()["upload_token"]}
    assert (
        client.post(base + "/requirement/upload-complete", json=token).status_code
        == 400
    )
    saved = client.post(base + "/task/upload-complete", json=token)
    assert saved.status_code == 201, saved.text
    replay = client.post(base + "/task/upload-complete", json=token)
    assert replay.status_code == 201 and replay.json() == saved.json()
    assert upload_key not in image_storage.objects
    assert len(image_storage.objects) == 1
