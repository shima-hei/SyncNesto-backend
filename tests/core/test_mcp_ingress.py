"""MCP専用の直接経路でも既存の公開環境保護を維持する。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.middleware import register_middleware

pytestmark = pytest.mark.no_db


def test_exact_mcp_exemptions_and_shared_rate_limit(monkeypatch):
    """パス例外を広げず、偽装IPヘッダーで回数制限を回避できない。"""
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "bff_shared_secret", "s" * 48)
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "allowed_hosts", ["testserver"])
    budgets = []

    def consume(*args):
        budgets.append(args)
        return 0

    monkeypatch.setattr("app.core.ingress.consume_request_budget", consume)
    app = FastAPI()

    @app.post("/oauth/token")
    @app.post("/oauth/other")
    @app.get("/integrations/mcp/connections")
    @app.get("/integrations/mcp/catalog")
    @app.post("/mcp")
    @app.post("/mcp/other")
    @app.get("/.well-known/oauth-protected-resource/mcp")
    def endpoint():
        return {"reached": True}

    register_middleware(app)
    client = TestClient(app)
    assert client.post("/oauth/other").status_code == 403
    assert client.get("/integrations/mcp/connections").status_code == 403
    assert client.get("/integrations/mcp/catalog").status_code == 200
    assert client.post("/mcp").status_code == 200
    assert client.post("/mcp/other").status_code == 403
    assert client.get("/.well-known/oauth-protected-resource/mcp").status_code == 200
    for index in range(10):
        assert (
            client.post(
                "/oauth/token", headers={"X-Syncnesto-Client-IP": f"192.0.2.{index}"}
            ).status_code
            == 200
        )
    assert client.post("/oauth/token").status_code == 429
    assert len({args[0] for args in budgets}) == 1
    assert all(args[2] is False for args in budgets)
    monkeypatch.setattr(settings, "mcp_enabled", False)
    assert client.get("/integrations/mcp/catalog").status_code == 403
    assert client.post("/mcp").status_code == 403
