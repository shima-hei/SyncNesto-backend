"""本文画像の確定と、公開固定版の取得境界を確認する。"""

from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image

from app.core.config import settings
from app.routers import body_images
from app.services.body_image import BodyImageService
from app.services.storage import StorageService
from tests.fakes.storage import MemoryS3Client
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_publications import create, publication_base
from tests.routers.test_artifact_releases import context as context  # noqa: F401
from tests.routers.test_publication_access import access_fixture as access_fixture  # noqa: F401
from tests.routers.test_publication_access import publish, shared_path


@pytest.fixture
def body_storage(monkeypatch):
    """添付バイトの保存は同じS3インターフェースを使用する。"""
    storage = StorageService(MemoryS3Client())
    monkeypatch.setattr(body_images, "service", BodyImageService(storage))
    monkeypatch.setattr("app.services.storage.StorageService", lambda: storage)
    monkeypatch.setattr(
        "app.services.body_image.BodyImageService", lambda: BodyImageService(storage)
    )
    monkeypatch.setattr(settings, "file_upload_mode", "server")
    stream = BytesIO()
    Image.new("RGB", (12, 8), "blue").save(stream, format="PNG")
    return stream.getvalue()


def test_shared_image_requires_the_published_node_and_current_grant(
    client,
    db,
    access_fixture,
    body_storage,
    create_test_project,
    assign_project_role,
):
    """未確定・別用途・別案件の画像は確定できず、外部には固定版だけを配信する。"""
    ctx = access_fixture
    project = ctx["project"]
    root = f"/projects/{project.id}"
    upload = f"{root}/body-images/requirement/upload"
    file = ("sample.png", body_storage, "image/png")
    assert (
        client.post(
            upload, files={"file": (file[0], file[1][:16], file[2])}
        ).status_code
        == 400
    )
    first = client.post(upload, files={"file": file})
    assert first.status_code == 201, first.text
    image_id = first.json()["id"]
    marker = f"![記録](/api/body-images/{image_id})"
    section = ctx["section"]
    url = f"{root}/requirement-sections/{section.id}"
    pending = f"![処理中](/api/body-images/pending/{uuid4()})"
    assert (
        client.patch(
            url, json={"version": section.version, "content": pending}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{root}/documents", json={"title": "別用途", "body": marker}
        ).status_code
        == 400
    )
    other = create_test_project()
    assign_project_role(user=ctx["user"], project=other, role_key="project_admin")
    assert (
        client.post(
            f"/projects/{other.id}/requirement-documents",
            json={"title": "別案件", "document_code": "OTHER", "purpose": marker},
        ).status_code
        == 400
    )
    saved = client.patch(
        url, json={"version": section.version, "content": f"前\n\n{marker}\n\n後"}
    )
    assert saved.status_code == 200, saved.text
    db.refresh(section)
    assert client.get(f"/body-images/{image_id}").content == body_storage
    assert client.get(f"{root}/comment-images/{image_id}").status_code == 404
    publication, _ = create(client, ctx)
    ctx["publication"] = publication
    publish(client, ctx)

    draft = client.post(upload, files={"file": file})
    assert draft.status_code == 201, draft.text
    draft_id = draft.json()["id"]
    assert (
        client.patch(
            url,
            json={
                "version": section.version,
                "content": f"![最新版](/api/body-images/{draft_id})",
            },
        ).status_code
        == 200
    )
    assert (
        client.delete(f"{root}/body-images/requirement/{image_id}").status_code == 404
    )

    authorize_as(client, ctx["guests"][0][0])
    items = client.get(shared_path(ctx, "/nodes")).json()["items"]
    projected = next(item for item in items if item["node_kind"] == "section")
    path = shared_path(ctx, f"/nodes/{projected['id']}/body-images")
    content = client.get(f"{path}/{image_id}")
    assert content.status_code == 200, content.text
    assert content.content == body_storage
    assert content.headers["cache-control"] == "private, no-store"
    assert client.get(f"{path}/{draft_id}").status_code == 404
    assert (
        client.get(
            shared_path(ctx, f"/nodes/{items[0]['id']}/body-images/{image_id}")
        ).status_code
        == 404
    )
    assert client.get(f"/body-images/{image_id}").status_code in {403, 404}

    authorize_as(client, ctx["user"])
    identity = publication["content"]["id"]
    grant = client.get(f"{publication_base(ctx)}/{identity}/grants").json()["items"][0]
    response = client.post(
        f"{publication_base(ctx)}/{identity}/grants/{grant['id']}/revoke",
        json={"request_id": str(uuid4()), "version": 1, "reason": "確認期間終了"},
    )
    assert response.status_code == 200, response.text
    authorize_as(client, ctx["guests"][0][0])
    assert client.get(f"{path}/{image_id}").status_code == 404


def test_mockup_upload_uses_private_storage_and_keeps_fixed_image(
    client, db, access_fixture, body_storage, create_test_project, assign_project_role
):
    """同じ案件の画像だけを埋め込み、固定後のHTML変更から画像を保つ。"""
    ctx = access_fixture
    project = ctx["project"]
    root = f"/projects/{project.id}"
    upload = client.post(
        f"{root}/body-images/specification/upload",
        files={"file": ("layout.png", body_storage, "image/png")},
    )
    assert upload.status_code == 201, upload.text
    image_id = upload.json()["id"]
    markup = f'<img src="/api/body-images/{image_id}" alt="モックの画像">'
    mockup = {"id": str(uuid4()), "name": "受付", "html": markup}
    preview_path = f"{root}/design-workspace/mockup-preview"
    preview = client.post(preview_path, json=mockup)
    assert preview.status_code == 200, preview.text
    assert "data:image/png;base64," in preview.json()["document"]
    assert image_id not in preview.json()["document"]
    assert (
        client.post(preview_path, json={**mockup, "html": markup * 9}).status_code
        == 400
    )

    other = create_test_project()
    assign_project_role(user=ctx["user"], project=other, role_key="project_admin")
    preview = client.post(
        f"/projects/{other.id}/design-workspace/mockup-preview", json=mockup
    )
    assert preview.status_code == 200, preview.text
    assert "data:image/png;" not in preview.json()["document"]
    assert image_id not in preview.json()["document"]
    body = {"title": "受付画面", "content": {"type": "screen", "mockups": [mockup]}}
    assert (
        client.post(f"/projects/{other.id}/specifications", json=body).status_code
        == 400
    )
    path = f"{root}/specifications/{ctx['spec'].id}"
    editor = client.get(path).json()
    original = {
        **{key: editor[key] for key in ("title", "summary", "content", "version")},
        "content": {**editor["content"], "mockups": [mockup]},
    }
    saved = client.patch(path, json=original)
    assert saved.status_code == 200, saved.text
    db.refresh(ctx["spec"])
    assert (
        client.delete(f"{root}/body-images/specification/{image_id}").status_code == 404
    )
    publication, _ = create(client, ctx)
    ctx["publication"] = publication
    publish(client, ctx)
    changed = client.patch(
        path,
        json={
            **original,
            "version": saved.json()["version"],
            "content": {**original["content"], "mockups": []},
        },
    )
    assert changed.status_code == 200, changed.text

    authorize_as(client, ctx["guests"][0][0])
    fixed = client.get(shared_path(ctx, "/nodes")).json()
    node = next(node for node in fixed["items"] if node["node_kind"] == "specification")
    resource = shared_path(ctx, f"/nodes/{node['id']}/mockups/{mockup['id']}/preview")
    preview = client.get(resource, params={"revision": fixed["authorization_revision"]})
    assert preview.status_code == 200, preview.text
    assert "data:image/png;base64," in preview.json()["document"]
    assert image_id not in preview.json()["document"]
    image = client.get(shared_path(ctx, f"/nodes/{node['id']}/body-images/{image_id}"))
    assert image.status_code == 200 and image.content == body_storage
