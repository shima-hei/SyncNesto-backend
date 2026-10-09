"""既存FastAPIへ組み込む、通常DB限定のstateless HTTP MCP。"""

import logging
from contextlib import contextmanager
from urllib.parse import urlsplit

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.core.exception_handlers import get_status_code
from app.core.exceptions import AppError, NotFoundError
from app.core.mcp import McpOAuthError, require_mcp_enabled
from app.db.mcp import get_mcp_db
from app.db.session import get_normal_db
from app.schemas.mcp_tools import McpOperation
from app.services.mcp_auth import McpAuthService
from app.services.mcp_operations import McpOperationsService
from syncnesto_mcp.server import SyncnestoMCP, operation_error

logger = logging.getLogger(__name__)


class McpUnavailable(Exception):
    """認証基盤の障害を、無効な資格情報と区別して返す。"""


class EmbeddedVerifier(TokenVerifier):
    """ローカルadapterと同じ資格情報交換を、通常DBのServiceで行う。"""

    async def verify_token(self, token: str) -> AccessToken | None:
        """元のaccessを都度検証し、通常JWTと用途を分けた短命JWTへ交換する。"""

        def exchange() -> AccessToken:
            with contextmanager(get_normal_db)() as db:
                value = McpAuthService().exchange(db, token)
                return AccessToken(
                    token=value["access_token"],
                    client_id=value["client_id"],
                    scopes=value["scope"].split(),
                    expires_at=value["expires_at"],
                    resource=settings.mcp_resource_url,
                )

        try:
            return await run_in_threadpool(exchange)
        except AppError:
            return None
        except Exception as exc:
            logger.error("MCP authentication failed: type=%s", type(exc).__name__)
            raise McpUnavailable() from None


class EmbeddedMCP(SyncnestoMCP):
    """共通の28ツールを、自己宛HTTP通信なしで既存Serviceへ渡す。"""

    async def request(self, path: str, *, payload: dict | None = None) -> dict:
        """既存の認可・savepoint・監査・再送結果のtransactionを再利用する。"""
        access = get_access_token()
        if access is None:
            raise operation_error(401)

        def execute() -> dict:
            with contextmanager(get_mcp_db)() as db:
                connection, user = McpAuthService().authenticate_api(db, access.token)
                operations = McpOperationsService()
                if path == "/integrations/mcp/catalog" and payload is None:
                    return {"tools": operations.catalog(db, connection, user)}
                if path != "/integrations/mcp/operations" or payload is None:
                    raise NotFoundError()
                return operations.execute(
                    db, connection, user, McpOperation.model_validate(payload)
                )

        try:
            return await run_in_threadpool(execute)
        except AppError as exc:
            status = (
                exc.status_code
                if isinstance(exc, McpOAuthError)
                else get_status_code(exc)
            )
            raise operation_error(status) from None
        except ValidationError:
            raise operation_error(422) from None
        except Exception as exc:
            # SDKへ例外本文・引数・SQL・資格情報を渡さない。
            logger.error("MCP operation failed: type=%s", type(exc).__name__)
            raise operation_error(500) from None


class McpBoundary:
    """SDKの入口でも機能フラグとCookie認証の分離を維持する。"""

    def __init__(self, app: ASGIApp) -> None:
        """SDKのASGI appを包む。"""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """同意・管理APIは既存Router、MCP本体はBearerのみで受け付ける。"""
        if scope["type"] == "http":
            try:
                require_mcp_enabled()
                if scope["path"] not in {
                    "/mcp",
                    "/.well-known/oauth-protected-resource/mcp",
                }:
                    raise NotFoundError()
            except NotFoundError:
                await JSONResponse({"message": "Not Found"}, status_code=404)(
                    scope, receive, send
                )
                return
            if Request(scope).cookies:
                await JSONResponse(
                    {"error": "invalid_request"},
                    status_code=400,
                    headers={"Cache-Control": "no-store"},
                )(scope, receive, send)
                return
            if scope["path"] == "/mcp" and scope["method"] in {"GET", "DELETE"}:
                # SDKのlegacy GET SSEを開かず、VercelではPOSTのJSON応答だけ使う。
                await JSONResponse(
                    {"error": "method_not_allowed"},
                    status_code=405,
                    headers={"Allow": "POST", "Cache-Control": "no-store"},
                )(scope, receive, send)
                return
        try:
            await self.app(scope, receive, send)
        except McpUnavailable:
            await JSONResponse(
                {"error": "temporarily_unavailable"},
                status_code=503,
                headers={"Cache-Control": "no-store", "Retry-After": "30"},
            )(scope, receive, send)


def create_embedded_mcp() -> Starlette:
    """同一originへ公開し、セッションや長寿命SSEをプロセス内に保持しない。"""
    server = EmbeddedMCP(
        settings.mcp_issuer_url.rstrip("/"),
        settings.mcp_resource_url,
        token_verifier=EmbeddedVerifier(),
    )
    resource = urlsplit(settings.mcp_resource_url)
    sdk_app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        max_request_body_size=270336,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[resource.netloc],
            allowed_origins=[f"{resource.scheme}://{resource.netloc}"],
        ),
    )
    sdk_app.add_middleware(McpBoundary)
    return sdk_app
