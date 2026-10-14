"""レビュー長文の変更検出と、匿名デモの設計書回収。"""

from uuid import UUID

from sqlalchemy import func, select

from app.core.config import settings
from app.models.project import Project
from app.models.specification import (
    Specification,
    SpecificationComment,
    SpecificationLink,
    SpecificationRevision,
)
from app.services.demo import DemoService
from app.services.storage import StorageService
from tests.fakes.storage import MemoryS3Client
from tests.routers.test_specifications import context as context
from tests.routers.test_specifications import create, update_data


def test_long_review_target_change_and_paging(client, context):
    """長文の末尾の変更も検知し、レビューの転送量を制限する。"""
    _, _, base = context
    row = create(client, base)
    url = f"{base}/{row['id']}"
    data = update_data(row)
    data["content"]["notes"] = "あ" * 900 + "末尾"
    row = client.patch(url, json=data).json()
    for i in range(2):
        response = client.post(
            url + "/comments",
            json={
                "specification_version": row["version"],
                "field_path": "content/notes",
                "body": f"指摘{i}",
            },
        )
        assert response.status_code == 201, response.text
    first = client.get(url + "/comments", params={"page": 1, "page_size": 1}).json()
    second = client.get(url + "/comments", params={"page": 2, "page_size": 1}).json()
    assert first[0]["id"] != second[0]["id"]
    assert len(first[0]["field_snapshot"]) == 800
    assert first[0]["author_name"]
    data = update_data(row)
    data["content"]["notes"] = "あ" * 900 + "修正後"
    assert client.patch(url, json=data).status_code == 200
    assert client.get(url + "/comments").json()[0]["anchor_changed"] is True


def test_demo_designs_are_collected(client, db, demo_settings, monkeypatch):
    """新規4表も既存の所有経路に従ってデモ終了後に回収する。"""
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "frontend_public_url", "http://testserver")
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
    base = f"/projects/{project.id}/specifications"
    row = create(client, base)
    target = client.get(
        base + "/link-candidates", params={"target_type": "task"}
    ).json()[0]
    assert (
        client.post(
            f"{base}/{row['id']}/links",
            json={"target_type": "task", "target_id": target["target_id"]},
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"{base}/{row['id']}/comments",
            json={
                "specification_version": 1,
                "field_path": "title",
                "body": "名称を確認",
            },
        ).status_code
        == 201
    )
    demo_id = UUID(started.json()["id"])
    DemoService().revoke(db, demo_id, "test")
    DemoService().cleanup(demo_id, StorageService(MemoryS3Client()))
    for model in (
        Specification,
        SpecificationComment,
        SpecificationLink,
        SpecificationRevision,
    ):
        assert db.scalar(select(func.count()).select_from(model)) == 0
