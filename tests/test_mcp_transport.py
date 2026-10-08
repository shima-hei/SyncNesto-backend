"""実際の公式SDK HTTP transportを起動し、境界とtool dispatchを検証する。"""

import pytest
from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient

from syncnesto_mcp.server import BackendVerifier, SyncnestoMCP, create_server

pytestmark = pytest.mark.no_db
RESOURCE = "http://127.0.0.1:8765/mcp"


@pytest.fixture
def transport(monkeypatch):
    """資格情報交換だけをfakeにし、SDKの認証・JSON-RPCは実物を通す。"""

    async def verify(self, token):
        if token != "mcp-only":
            return None
        return AccessToken(
            token="api-only",
            client_id="syncnesto-codex-local",
            scopes=["mcp:work"],
            resource=RESOURCE,
        )

    async def request(self, path, *, payload=None):
        from mcp.server.auth.middleware.auth_context import get_access_token

        access = get_access_token()
        assert access is not None and access.token == "api-only"
        if path.endswith("catalog"):
            return {
                "tools": [
                    {
                        "name": "list_projects",
                        "description": "Projects",
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                    }
                ]
            }
        assert payload == {"tool": "list_projects", "arguments": {}}
        return {"items": [{"id": 1}]}

    monkeypatch.setattr(BackendVerifier, "verify_token", verify)
    monkeypatch.setattr(SyncnestoMCP, "request", request)
    with TestClient(
        create_server("http://127.0.0.1:8000", RESOURCE),
        base_url="http://127.0.0.1:8765",
    ) as client:
        yield client


def test_discovery_auth_host_origin_and_tools(transport):
    """Host/Origin・401 metadata・初期化・toolsを同じ実装で検証する。"""
    headers = {"Accept": "application/json, text/event-stream"}
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    unauth = transport.post("/mcp", json=request, headers=headers)
    assert unauth.status_code == 401
    assert "resource_metadata=" in unauth.headers["www-authenticate"]
    metadata = transport.get("/.well-known/oauth-protected-resource/mcp")
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["resource"] == RESOURCE
    assert metadata.json()["authorization_servers"] == ["http://127.0.0.1:8000/"]
    headers["Authorization"] = "Bearer mcp-only"
    assert (
        transport.post(
            "/mcp", json=request, headers={**headers, "Host": "evil.test"}
        ).status_code
        == 421
    )
    assert (
        transport.post(
            "/mcp", json=request, headers={**headers, "Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    result = transport.post("/mcp", json=request, headers=headers)
    assert result.status_code == 200, result.text
    headers["MCP-Protocol-Version"] = result.json()["result"]["protocolVersion"]
    listed = transport.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["result"]["tools"][0]["name"] == "list_projects"
    called = transport.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_projects", "arguments": {}},
        },
        headers=headers,
    )
    assert called.status_code == 200, called.text
    assert called.json()["result"]["structuredContent"] == {"items": [{"id": 1}]}


@pytest.mark.parametrize(
    "api,resource",
    [
        ("http://evil.test", RESOURCE),
        ("https://api.test/other", RESOURCE),
        ("https://user:pass@api.test", RESOURCE),
        ("http://127.0.0.1:8000", "http://0.0.0.0:8765/mcp"),
    ],
)
def test_reject_nonlocal_resource_and_unsafe_api(api, resource):
    """ローカルMCPの意図しない公開・資格情報転送先を拒否する。"""
    with pytest.raises(ValueError):
        create_server(api, resource)
