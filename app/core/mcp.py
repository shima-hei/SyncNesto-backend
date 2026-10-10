"""通常Cookieと分離したMCPの認証境界。"""

import hashlib
import hmac
import re
from urllib.parse import urlsplit

from app.core.config import settings
from app.core.exceptions import AppError, NotFoundError

CLIENT_ID = "syncnesto-codex-local"
PLUGIN_CLIENT_ID = "syncnesto-openai-plugin"
CLIENT_IDS = frozenset({CLIENT_ID, PLUGIN_CLIENT_ID})
SCOPES = ["mcp:work"]


def valid_redirect_uri(client_id: str, value: str) -> bool:
    """既存clientはloopback、pluginは登録済みOpenAI callbackも許可する。"""
    if (
        client_id not in CLIENT_IDS
        or len(value) > 500
        or "\\" in value
        or any(ord(char) <= 32 or ord(char) == 127 for char in value)
    ):
        return False
    try:
        uri = urlsplit(value)
        port = uri.port
    except ValueError:
        return False
    if uri.username or uri.password or uri.query or uri.fragment:
        return False
    if (
        uri.scheme == "http"
        and uri.hostname == "127.0.0.1"
        and port is not None
        and 1 <= port <= 65535
        and re.fullmatch(r"/callback(?:/[A-Za-z0-9_-]{1,100})?", uri.path)
    ):
        return True
    return bool(
        client_id == PLUGIN_CLIENT_ID
        and value in settings.mcp_plugin_redirect_uris
        and uri.scheme == "https"
        and uri.netloc == "chatgpt.com"
        and (
            uri.path == "/connector_platform_oauth_redirect"
            or re.fullmatch(r"/connector/oauth/[A-Za-z0-9_-]{1,200}", uri.path)
        )
    )


class McpOAuthError(AppError):
    """秘密を含めずOAuth標準のエラーを返す。"""

    code = "MCP_OAUTH_ERROR"

    def __init__(self, error: str = "invalid_grant", status_code: int = 400):
        """クライアント向けの種別を設定する。"""
        super().__init__("MCPの接続を確認できません。接続をやり直してください")
        self.error = error
        self.status_code = status_code


def require_mcp_enabled() -> None:
    """公開環境は同一originのHTTPS resource、開発時はloopbackを許可する。"""
    if not settings.mcp_enabled:
        raise NotFoundError()
    try:
        resource = urlsplit(settings.mcp_resource_url)
        issuer = urlsplit(settings.mcp_issuer_url)
        resource_port = resource.port
        issuer.port
    except ValueError as exc:
        raise NotFoundError() from exc
    embedded = resource.scheme == issuer.scheme and resource.netloc == issuer.netloc
    local = (
        not settings.is_public_environment
        and resource.scheme == "http"
        and resource.hostname == "127.0.0.1"
        and resource_port is not None
    )
    if (
        not (embedded or local)
        or resource.path != "/mcp"
        or resource.username
        or resource.password
        or resource.query
        or resource.fragment
        or issuer.username
        or issuer.password
        or issuer.query
        or issuer.fragment
        or issuer.path not in {"", "/"}
        or not issuer.hostname
        or (settings.is_public_environment and issuer.scheme != "https")
        or issuer.scheme not in {"http", "https"}
        or (
            issuer.scheme == "http"
            and issuer.hostname not in {"127.0.0.1", "localhost"}
        )
    ):
        raise NotFoundError()


def api_audience() -> str:
    """連携API専用のaudienceを返す。"""
    return settings.mcp_issuer_url.rstrip("/") + "/integrations/mcp"


def oauth_issuer() -> str:
    """SDKのAnyHttpUrlによるoriginの正規化と完全一致させる。"""
    return settings.mcp_issuer_url.rstrip("/") + "/"


def api_signing_key() -> bytes:
    """通常ログインJWTと用途を分けた鍵を導出する。"""
    return hmac.new(
        settings.secret_key.encode(), b"syncnesto:mcp:api:v1", hashlib.sha256
    ).digest()


def digest(value: str) -> str:
    """高エントロピー資格情報の検索用ハッシュ。"""
    return hashlib.sha256(value.encode()).hexdigest()


def is_direct_mcp_request(path: str, method: str) -> bool:
    """BFF必須の例外を専用経路とメソッドに限定する。"""
    return settings.mcp_enabled and (method, path) in {
        ("GET", "/.well-known/oauth-protected-resource/mcp"),
        ("POST", "/mcp"),
        ("GET", "/mcp"),
        ("DELETE", "/mcp"),
        ("GET", "/.well-known/oauth-authorization-server"),
        ("GET", "/oauth/authorize"),
        ("POST", "/oauth/token"),
        ("POST", "/oauth/exchange"),
        ("POST", "/oauth/revoke"),
        ("GET", "/integrations/mcp/catalog"),
        ("POST", "/integrations/mcp/operations"),
    }
