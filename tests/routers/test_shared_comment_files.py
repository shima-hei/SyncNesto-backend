"""共有コメントの投稿、下書き、配信に同じ固定版の境界を適用する。"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.routers.artifact_publication_access import comments
from app.services.storage import StorageService
from tests.fakes.storage import MemoryS3Client
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_publications import create, publication_base
from tests.routers.test_artifact_releases import context as context  # noqa: F401
from tests.routers.test_comment_images import PNG
from tests.routers.test_publication_access import access_fixture as access_fixture  # noqa: F401
from tests.routers.test_publication_access import publish


@pytest.fixture
def comment_storage(monkeypatch):
    storage = MemoryS3Client()
    monkeypatch.setattr(
        comments.files, "storage_factory", lambda: StorageService(storage)
    )
    monkeypatch.setattr(settings, "file_upload_mode", "server")
    return storage


def upload(client, base, name, data, kind, nonce=None):
    return client.post(
        f"{base}/files",
        data={"request_id": nonce or str(uuid4())},
        files={"file": (name, data, kind)},
    )


def test_comment_files_bind_to_the_author_and_current_publication(
    client, access_fixture, comment_storage, monkeypatch
):
    ctx = access_fixture
    author, part = ctx["guests"][0]
    reader, reader_part = ctx["guests"][1]
    published, _, _ = publish(
        client,
        ctx,
        both=True,
        recipient_options={
            part.id: {"comment_allowed": True, "download_allowed": False},
            reader_part.id: {"download_allowed": True},
        },
    )
    identity = published["id"]
    base = f"/shared/publications/{identity}/comments"
    internal = f"{publication_base(ctx)}/{identity}/comments"
    admin = TestClient(client.app)
    authorize_as(admin, ctx["user"])
    authorize_as(client, reader)
    assert upload(client, base, "proof.png", PNG, "image/png").status_code == 403
    authorize_as(client, author)
    oversized = client.post(
        base + "/files/plan",
        json={
            "filename": "proof.png",
            "content_type": "image/png",
            "byte_size": 3 * 1024 * 1024 + 1,
        },
    )
    assert oversized.status_code == 400, oversized.text
    assert (
        upload(client, base, "proof.png", b"not an image", "image/png").status_code
        == 400
    )
    nonce = str(uuid4())
    created = upload(client, base, "proof.png", PNG, "image/png", nonce)
    assert created.status_code == 201, created.text
    image = created.json()
    assert not ({"storage_key", "uploaded_by", "grant_id", "project_id"} & image.keys())
    assert upload(client, base, "proof.png", PNG, "image/png", nonce).json() == image
    assert (
        upload(
            client, base, "named-differently.png", PNG, "image/png", nonce
        ).status_code
        == 409
    )
    note = upload(client, base, "補足.txt", b"review evidence", "text/plain")
    assert note.status_code == 201, note.text
    note_id = note.json()["id"]
    request = {
        "request_id": str(uuid4()),
        "body": "確認のため添付します",
        "attachment_ids": [image["id"], note_id],
    }
    assert (
        client.get(
            base + f"/{uuid4()}/files/{image['id']}/content?inline=true"
        ).status_code
        == 404
    )
    assert admin.post(internal, json=request).status_code == 404
    assert admin.get(internal).json()["items"] == []
    posted = client.post(base, json=request)
    assert posted.status_code == 201, posted.text
    comment = posted.json()
    assert [file["id"] for file in comment["attachments"]] == request["attachment_ids"]
    assert client.post(base, json=request).json() == comment
    assert client.delete(base + f"/files/{image['id']}").status_code == 404
    path = f"{base}/{comment['id']}/files"
    image_path = path + f"/{image['id']}/content?inline=true"
    shown = client.get(image_path)
    assert shown.status_code == 200 and shown.content == PNG
    assert shown.headers["cache-control"] == "private, no-store"
    assert shown.headers["content-type"] == "image/png"
    assert client.get(path + f"/{note_id}/content").status_code == 404
    reply_file = upload(client, base, "返信.png", PNG, "image/png").json()["id"]
    reply = client.post(
        base,
        json={
            "parent_id": comment["id"],
            "body": "返信の添付",
            "attachment_ids": [reply_file],
            "request_id": str(uuid4()),
        },
    )
    assert reply.status_code == 201, reply.text
    reply_id = reply.json()["id"]
    authorize_as(client, reader)
    assert client.get(base).json()["items"] == [comment]
    assert client.get(base, params={"parent_id": comment["id"]}).json()["items"] == [
        reply.json()
    ]
    assert (
        client.get(
            f"{base}/{reply_id}/files/{reply_file}/content?inline=true"
        ).status_code
        == 200
    )
    assert client.get(f"{path}/{reply_file}/content?inline=true").status_code == 404
    downloaded = client.get(path + f"/{note_id}/content")
    assert downloaded.content == b"review evidence"
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert client.get(path + f"/{note_id}/content?inline=true").status_code == 404
    assert client.get(image_path).status_code == 200

    grants = admin.get(f"{publication_base(ctx)}/{identity}/grants").json()["items"]
    grant = next(
        row for row in grants if row["participation_id"] == str(reader_part.id)
    )
    stored = comment_storage.get_object

    def read_then_revoke(**options):
        result = stored(**options)
        revoked = admin.post(
            f"{publication_base(ctx)}/{identity}/grants/{grant['id']}/revoke",
            json={
                "request_id": str(uuid4()),
                "version": grant["version"],
                "reason": "検証",
            },
        )
        assert revoked.status_code == 200, revoked.text
        return result

    monkeypatch.setattr(comment_storage, "get_object", read_then_revoke)
    assert client.get(image_path).status_code == 404
    monkeypatch.setattr(comment_storage, "get_object", stored)
    assert client.get(base).status_code == 404
    assert client.get(image_path).status_code == 404
    authorize_as(client, author)
    assert client.get(image_path).status_code == 200


def test_presigned_comment_file_is_bound_to_the_publication_and_replayable(
    client, access_fixture, comment_storage, monkeypatch
):
    ctx = access_fixture
    actor, part = ctx["guests"][0]
    first, _, _ = publish(
        client, ctx, recipient_options={part.id: {"comment_allowed": True}}
    )
    ctx["publication"], _ = create(client, ctx)
    second, _, _ = publish(
        client, ctx, recipient_options={part.id: {"comment_allowed": True}}
    )
    authorize_as(client, actor)
    monkeypatch.setattr(settings, "file_upload_mode", "presigned")
    base = f"/shared/publications/{first['id']}/comments/files"
    staged = client.post(
        base + "/plan",
        json={
            "filename": "proof.png",
            "content_type": "image/png",
            "byte_size": len(PNG),
        },
    )
    assert staged.status_code == 200, staged.text
    key = comment_storage.presigned_params["Key"]
    comment_storage.objects[key] = (PNG, "image/png")
    token = {"upload_token": staged.json()["upload_token"]}
    wrong = client.post(
        f"/shared/publications/{second['id']}/comments/files/complete", json=token
    )
    assert wrong.status_code == 400, wrong.text
    saved = client.post(base + "/complete", json=token)
    assert saved.status_code == 201, saved.text
    assert client.post(base + "/complete", json=token).json() == saved.json()
    assert key not in comment_storage.objects and len(comment_storage.objects) == 1
    assert client.delete(base + f"/{saved.json()['id']}").status_code == 204
    assert not comment_storage.objects
