"""目標を含む新固定形式でも、共有の本人認可・停止を維持する。"""

from uuid import uuid4

from app.core.config import settings
from tests.helpers.auth import authorize_as
from tests.routers.test_artifact_publications import publication_base
from tests.routers.test_artifact_releases import context as context  # noqa: F401
from tests.routers.test_artifact_releases import save
from tests.routers.test_publication_access import (
    access_fixture as access_fixture,  # noqa: F401
)
from tests.routers.test_publication_access import (
    denied,
    publish,
    shared_path,
)


def test_v2_objectives_shared_read_and_withdraw(client, access_fixture, monkeypatch):
    """新本文は固定版のまま本人だけに公開し、停止後は全sharedから消す。"""
    ctx = access_fixture
    monkeypatch.setattr(settings, "project_objectives_enabled", True)
    values = {
        "project_goal": "同じゴールへ進む",
        "project_scope": "公開する対象範囲",
        "project_completion_criteria": "完了の判断条件",
    }
    response = client.patch(
        f"/projects/{ctx['project'].id}/requirement-documents/{ctx['doc'].id}",
        json={"version": ctx["doc"].version, **values},
    )
    assert response.status_code == 200
    release, _ = save(
        client,
        ctx,
        {
            "source": {
                "kind": "requirement_document",
                "id": ctx["doc"].id,
                "version": response.json()["version"],
            },
            "projection_version": "internal-preview-v2",
        },
    )
    request = {
        "release_id": release["id"],
        "request_id": str(uuid4()),
        "projection_version": "public-preview-v2",
    }
    preview = client.post(publication_base(ctx) + "/preview", json=request)
    assert preview.status_code == 200
    body = preview.json()
    response = client.post(
        publication_base(ctx),
        json={
            **request,
            "expected_public_digest": body["public_digest"],
            "expected_source_manifest_digest": body["source_manifest_digest"],
        },
    )
    assert response.status_code == 201
    ctx = {**ctx, "publication": response.json()}
    published, _, _ = publish(client, ctx)
    authorize_as(client, ctx["guests"][0][0])
    metadata = client.get(shared_path(ctx))
    assert metadata.status_code == 200
    assert metadata.json()["content"]["projection_version"] == "public-preview-v2"
    page = client.get(shared_path(ctx, "/nodes"))
    assert page.status_code == 200
    assert page.json()["projection_version"] == "public-preview-v2"
    payload = page.json()["items"][0]["payload"]
    assert all(payload[key] == value for key, value in values.items())
    assert "goal_document_id" not in page.text
    assert client.get(f"/projects/{ctx['project'].id}/objectives").status_code == 403
    authorize_as(client, ctx["user"])
    stopped = client.post(
        publication_base(ctx) + f"/{published['id']}/withdraw",
        json={"request_id": str(uuid4()), "version": 2, "reason": "確認を終了"},
    )
    assert stopped.status_code == 200
    authorize_as(client, ctx["guests"][0][0])
    denied(client, ctx)
