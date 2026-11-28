"""ローカルMCPの設計項目・一括保存・従来カタログの境界。"""

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.mcp import PLUGIN_CLIENT_ID
from app.models.mcp import McpConnection
from app.models.user import User
from app.services.mcp_operations import McpOperationsService
from tests.routers.test_mcp import api_headers, connect, operate
from tests.routers.test_mcp import context as context


@pytest.fixture
def design_mcp(client, context, monkeypatch):
    monkeypatch.setattr(settings, "mcp_design_tools_enabled", True)
    project = context[1]
    direct, tokens, _ = connect(client, project.id)
    headers = api_headers(direct, tokens["access_token"])

    def call(name, **arguments):
        return operate(direct, headers, name, project.id, **arguments)

    return call


def test_items_and_bulk_patch_keep_versions_and_are_atomic(design_mcp):
    call = design_mcp
    field = str(uuid4())
    mockup = str(uuid4())
    screen_response = call(
        "create_design_artifact",
        data={
            "title": "画面",
            "content": {
                "type": "screen",
                "route": "/test",
                "fields": [{"id": field, "name": "旧項目"}],
                "mockups": [
                    {
                        "id": mockup,
                        "name": "レイアウト",
                        "html": "HTMLは明示取得",
                        "css": "/* CSS */",
                    }
                ],
            },
        },
    )
    assert screen_response.status_code == 200, screen_response.text
    screen = screen_response.json()["id"]
    table_response = call(
        "create_design_artifact",
        data={"title": "テーブル", "content": {"type": "database"}},
    )
    assert table_response.status_code == 200, table_response.text
    table = table_response.json()["id"]
    column = str(uuid4())
    changes = [
        {
            "design_id": screen,
            "version": 1,
            "items": [
                {
                    "section": "fields",
                    "item_id": field,
                    "mode": "update",
                    "values": {"name": "新項目"},
                }
            ],
        },
        {
            "design_id": table,
            "version": 1,
            "items": [
                {
                    "section": "columns",
                    "item_id": column,
                    "mode": "add",
                    "values": {"physical_name": "identifier", "data_type": "integer"},
                }
            ],
        },
    ]
    invalid = {
        **changes[1],
        "items": [{**changes[1]["items"][0], "values": {"data_type": "unknown_type"}}],
    }
    assert (
        call("patch_design_artifacts", updates=[changes[0], invalid]).status_code == 400
    )
    before = call(
        "get_design_items", key=False, design_id=screen, section="fields"
    ).json()
    assert before["version"] == 1 and before["items"][0]["name"] == "旧項目"
    key = uuid4().hex
    saved = call("patch_design_artifacts", key=key, updates=changes)
    assert saved.status_code == 200, saved.text
    assert [item["version"] for item in saved.json()["items"]] == [2, 2]
    assert (
        call("patch_design_artifacts", key=key, updates=changes).json() == saved.json()
    )
    assert call("patch_design_artifacts", updates=changes).status_code == 409
    fixed = call(
        "get_design_items",
        key=False,
        design_id=screen,
        section="fields",
        revision=1,
        item_ids=[field],
        page_size=1,
    ).json()
    assert fixed["source"] == "revision" and fixed["items"][0]["name"] == "旧項目"
    current = call(
        "get_design_items", key=False, design_id=screen, section="fields"
    ).json()
    assert (
        current["items"][0]["id"] == field and current["items"][0]["name"] == "新項目"
    )
    mockups = call(
        "get_design_items", key=False, design_id=screen, section="mockups"
    ).json()["items"]
    assert (
        mockups[0]["id"] == mockup
        and "html" not in mockups[0]
        and "css" not in mockups[0]
    )
    raw = call(
        "get_design_artifact", key=False, design_id=screen, section="mockups"
    ).json()["mockups"]
    assert raw[0]["html"] == "HTMLは明示取得"


def test_collection_settings_and_new_tools_use_local_gate(design_mcp, monkeypatch):
    call = design_mcp
    created = call(
        "create_design_collection",
        data={
            "kind": "api_set",
            "name": "API",
            "configuration": {"info": {"title": "API", "version": "1"}},
        },
    )
    assert created.status_code == 200, created.text
    collection_id = created.json()["id"]
    stored = call(
        "get_design_collection", key=False, collection_id=collection_id, kind="api_set"
    ).json()
    assert stored["configuration"]["info"]["version"] == "1"
    monkeypatch.setattr(settings, "mcp_design_tools_enabled", False)
    assert (
        call(
            "get_design_collection",
            key=False,
            collection_id=collection_id,
            kind="api_set",
        ).status_code
        == 404
    )
    monkeypatch.setattr(settings, "mcp_design_tools_enabled", True)
    service = McpOperationsService()
    monkeypatch.setattr(service, "allowed", lambda *args: True)
    with Session() as db:
        catalog = service.catalog(
            db, McpConnection(project_ids=[1], client_id=PLUGIN_CLIENT_ID), User()
        )
    assert not {"get_design_items", "patch_design_artifacts"} & {
        item["name"] for item in catalog
    }
