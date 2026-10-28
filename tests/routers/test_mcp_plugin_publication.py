"""実際の公開OAuth clientでも既存の文書・同意・停止の境界を通る。"""

from uuid import uuid4

from app.core.config import settings
from app.core.mcp import PLUGIN_CLIENT_ID
from tests.routers.test_mcp import api_headers, connect, operate
from tests.routers.test_mcp import context as context


def test_public_client_document_patch_replay_and_flag_rollback(
    client, context, monkeypatch
):
    """公開接続で文書を往復し、再送・未同意案件・停止後の直接callを検証する。"""
    monkeypatch.setattr(settings, "mcp_development_tools_enabled", False)
    monkeypatch.setattr(settings, "mcp_plugin_extended_tools_enabled", True)
    project = context[1]
    direct, tokens, _ = connect(client, project.id, client_id=PLUGIN_CLIENT_ID)
    headers = api_headers(direct, tokens["access_token"])
    created = operate(
        direct,
        headers,
        "create_project_document",
        project.id,
        data={"title": "公開接続の検証", "body": "決定😀"},
    )
    assert created.status_code == 200, created.text
    document_id = created.json()["id"]
    outline = operate(
        direct,
        headers,
        "get_project_document_content",
        project.id,
        key=False,
        document_id=document_id,
        mode="outline",
    )
    assert outline.status_code == 200, outline.text
    assert outline.json()["body_length"] == 3
    arguments = {
        "document_id": document_id,
        "version": outline.json()["version"],
        "edits": [{"start": 2, "end": 3, "expected_text": "😀", "replacement": "MFA"}],
        "key": uuid4().hex,
    }
    patched = operate(
        direct, headers, "patch_project_document", project.id, **arguments
    )
    assert patched.status_code == 200, patched.text
    assert (
        operate(
            direct, headers, "patch_project_document", project.id, **arguments
        ).json()
        == patched.json()
    )
    current = operate(
        direct,
        headers,
        "get_project_document",
        project.id,
        key=False,
        document_id=document_id,
    )
    assert current.status_code == 200, current.text
    assert current.json()["body"] == "決定MFA"
    assert (
        operate(
            direct,
            headers,
            "get_project_document",
            project.id + 1000,
            key=False,
            document_id=document_id,
        ).status_code
        == 403
    )
    monkeypatch.setattr(settings, "mcp_plugin_extended_tools_enabled", False)
    assert (
        operate(
            direct, headers, "patch_project_document", project.id, **arguments
        ).status_code
        == 404
    )
    assert (
        operate(direct, headers, "list_tasks", project.id, key=False).status_code == 200
    )
