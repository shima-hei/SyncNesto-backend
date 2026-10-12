"""公開ドメイン確認用の値を、設定された場合だけplain textで返す。"""

import pytest

from app.core.config import settings


@pytest.mark.parametrize("value", ["", "invalid\nvalue"])
def test_unconfigured_domain_challenge_is_not_published(client, monkeypatch, value):
    """未設定や改行を含む誤設定は公開しない。"""
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_plugin_domain_challenge", value)
    assert client.get("/.well-known/openai-apps-challenge").status_code == 404


def test_domain_challenge_is_exact_plain_text(client, monkeypatch):
    """BFF秘密を持たない公開検証者へ、正確なplain textを返す。"""
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "bff_shared_secret", "s" * 48)
    monkeypatch.setattr(settings, "mcp_issuer_url", "https://api.example")
    monkeypatch.setattr(settings, "mcp_resource_url", "https://api.example/mcp")
    monkeypatch.setattr("app.core.ingress.consume_request_budget", lambda *args: 0)
    monkeypatch.setattr(settings, "mcp_plugin_domain_challenge", "review-domain_123")
    result = client.get("/.well-known/openai-apps-challenge")
    assert result.status_code == 200
    assert result.content == b"review-domain_123"
    assert result.headers["content-type"] == "text/plain; charset=utf-8"
    assert result.headers["cache-control"] == "no-store"
