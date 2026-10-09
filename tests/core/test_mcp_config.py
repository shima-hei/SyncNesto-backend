"""公開MCPのresourceを設定済みの同一HTTPS originに限定する。"""

import pytest

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.core.mcp import require_mcp_enabled

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize(
    "resource",
    [
        "http://127.0.0.1:8765/mcp",
        "http://api.example/mcp",
        "https://evil.example/mcp",
        "https://api.example:444/mcp",
        "https://api.example/mcp/",
        "https://api.example/other",
        "https://user:pass@api.example/mcp",
        "https://api.example/mcp?extra=1",
        "https://api.example/mcp#fragment",
        "https://api.example:bad/mcp",
    ],
)
def test_public_mcp_rejects_wrong_origin_or_resource(monkeypatch, resource):
    """公開時のloopback・別origin・別path・資格情報付きURLを拒否する。"""
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_issuer_url", "https://api.example")
    monkeypatch.setattr(settings, "mcp_resource_url", resource)
    with pytest.raises(NotFoundError):
        require_mcp_enabled()


def test_public_mcp_accepts_exact_https_resource_and_flag(monkeypatch):
    """同一originでもフラグ無効時は入口を開かない。"""
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_issuer_url", "https://api.example")
    monkeypatch.setattr(settings, "mcp_resource_url", "https://api.example/mcp")
    require_mcp_enabled()
    monkeypatch.setattr(settings, "mcp_enabled", False)
    with pytest.raises(NotFoundError):
        require_mcp_enabled()
