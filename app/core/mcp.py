"""通常Cookieと分離したMCPの認証境界。"""

import hashlib
import hmac
from urllib.parse import urlsplit

from app.core.config import settings
from app.core.exceptions import AppError, NotFoundError

CLIENT_ID = "syncnesto-codex-local"
SCOPES = ["mcp:work"]


class McpOAuthError(AppError):
    """秘密を含めずOAuth標準のエラーを返す。"""

    code = "MCP_OAUTH_ERROR"

    def __init__(self, error: str = "invalid_grant", status_code: int = 400):
        """クライアント向けの種別を設定する。"""
        super().__init__("MCPの接続を確認できません。接続をやり直してください")
        self.error = error
        self.status_code = status_code


def require_mcp_enabled() -> None:
    """設定済みのループバックresourceだけを有効にする。"""
    if not settings.mcp_enabled:
        raise NotFoundError()
    try:
        resource = urlsplit(settings.mcp_resource_url)
        issuer = urlsplit(settings.mcp_issuer_url)
        resource_port = resource.port
        issuer.port
    except ValueError as exc:
        raise NotFoundError() from exc
    if (
        resource.scheme != "http"
        or resource.hostname != "127.0.0.1"
        or resource.path != "/mcp"
        or resource_port is None
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
        ("GET", "/.well-known/oauth-authorization-server"),
        ("GET", "/oauth/authorize"),
        ("POST", "/oauth/token"),
        ("POST", "/oauth/exchange"),
        ("POST", "/oauth/revoke"),
        ("GET", "/integrations/mcp/catalog"),
        ("POST", "/integrations/mcp/operations"),
    }
