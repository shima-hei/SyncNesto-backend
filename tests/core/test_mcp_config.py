"""公開MCPのresourceを設定済みの同一HTTPS originに限定する。"""

import pytest

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.core.mcp import (
    CLIENT_ID,
    PLUGIN_CLIENT_ID,
    require_mcp_enabled,
    valid_redirect_uri,
)

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize(
    "callback",
    [
        "https://evil.example/connector_platform_oauth_redirect",
        "https://chatgpt.com.evil.example/connector/oauth/id",
        "https://user@chatgpt.com/connector/oauth/id",
        "https://chatgpt.com:443/connector/oauth/id",
        "https://chatgpt.com/connector/oauth/id?next=evil",
        "https://chatgpt.com/connector/oauth/id#fragment",
        "https://chatgpt.com/connector/oauth/id/other",
        "https://chatgpt.com/connector/oauth/%2e%2e",
        "https://chatgpt.com/other",
        "https://chatgpt.com/connector/oauth/id\n",
        "https://chatgpt.com\\@evil.example/connector/oauth/id",
    ],
)
def test_plugin_allowlist_cannot_enable_unsafe_callback(monkeypatch, callback):
    """allowlistの設定ミスでも任意origin・別path・正規化の抜け道を開かない。"""
    monkeypatch.setattr(settings, "mcp_plugin_redirect_uris", [callback])
    assert not valid_redirect_uri(PLUGIN_CLIENT_ID, callback)


def test_plugin_callback_needs_exact_registration(monkeypatch):
    """HTTPS callbackはplugin専用で、別IDや未登録の宛先へ拡大しない。"""
    callback = "https://chatgpt.com/connector/oauth/approved-id"
    monkeypatch.setattr(settings, "mcp_plugin_redirect_uris", [callback])
    assert valid_redirect_uri(PLUGIN_CLIENT_ID, callback)
    assert not valid_redirect_uri(CLIENT_ID, callback)
    assert not valid_redirect_uri("unknown", callback)
    assert not valid_redirect_uri(PLUGIN_CLIENT_ID, callback + "2")
    assert valid_redirect_uri(PLUGIN_CLIENT_ID, "http://127.0.0.1:54321/callback")


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
