"""OAuthの直接経路と、既存Cookieで保護する接続同意・管理。"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.mcp import SCOPES, McpOAuthError, oauth_issuer, require_mcp_enabled
from app.db.mcp import get_mcp_db
from app.db.session import get_db, get_normal_db
from app.models.user import User
from app.schemas.mcp import (
    McpConnectionRead,
    McpConsentCreate,
    McpConsentRead,
    McpRedirectRead,
)
from app.schemas.mcp_tools import McpOperation
from app.services.mcp_auth import McpAuthService
from app.services.mcp_operations import McpOperationsService

router = APIRouter(tags=["mcp"], dependencies=[Depends(require_mcp_enabled)])
service = McpAuthService()


def no_cookie(request: Request) -> None:
    """連携用直接経路でブラウザCookieを受け付けない。"""
    if request.cookies:
        raise McpOAuthError("invalid_request")


def api_identity(request: Request, db: Session = Depends(get_mcp_db)):
    """Cookieや通常ログインJWTでは業務連携APIを利用できない。"""
    no_cookie(request)
    scheme, _, credential = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not credential:
        raise McpOAuthError("invalid_token", 401)
    return service.authenticate_api(db, credential)


@router.get("/integrations/mcp/catalog", include_in_schema=False)
def catalog(
    response: Response,
    identity=Depends(api_identity),
    db: Session = Depends(get_mcp_db),
):
    """ローカルMCPへ現在の権限に対応するツール契約を返す。"""
    response.headers["Cache-Control"] = "no-store"
    connection, user = identity
    return {"tools": McpOperationsService().catalog(db, connection, user)}


@router.post("/integrations/mcp/operations", include_in_schema=False)
def operate(
    data: McpOperation,
    response: Response,
    identity=Depends(api_identity),
    db: Session = Depends(get_mcp_db),
):
    """業務操作とreceiptを一つのtransactionで実行する。"""
    response.headers["Cache-Control"] = "no-store"
    connection, user = identity
    return McpOperationsService().execute(db, connection, user, data)


@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
def metadata() -> dict:
    """固定のissuer・public client・PKCEを広告する。"""
    issuer = settings.mcp_issuer_url.rstrip("/")
    return {
        "issuer": oauth_issuer(),
        "authorization_endpoint": issuer + "/oauth/authorize",
        "token_endpoint": issuer + "/oauth/token",
        "revocation_endpoint": issuer + "/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": SCOPES,
        "authorization_response_iss_parameter_supported": True,
    }


@router.get("/oauth/authorize", include_in_schema=False)
def authorize(request: Request, db: Session = Depends(get_normal_db)):
    """本人のログイン・同意はフロントエンドで行う。"""
    if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise McpOAuthError("invalid_request")
    return RedirectResponse(
        service.start(db, dict(request.query_params)),
        status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/oauth/token", include_in_schema=False, dependencies=[Depends(no_cookie)])
async def token(
    request: Request, response: Response, db: Session = Depends(get_normal_db)
):
    """コード/refreshだけをフォームで交換する。"""
    params = await request.form(max_fields=10)
    if any(len(params.getlist(key)) != 1 for key in params):
        raise McpOAuthError("invalid_request")
    response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache"})
    return service.token(db, {key: str(value) for key, value in params.items()})


@router.post(
    "/oauth/exchange", include_in_schema=False, dependencies=[Depends(no_cookie)]
)
def exchange(
    request: Request, response: Response, db: Session = Depends(get_normal_db)
):
    """MCPのTokenVerifierが専用の資格情報に交換する。"""
    scheme, _, credential = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not credential:
        raise McpOAuthError("invalid_token", 401)
    response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache"})
    return service.exchange(db, credential)


@router.post(
    "/oauth/revoke", include_in_schema=False, dependencies=[Depends(no_cookie)]
)
async def revoke_token(request: Request, db: Session = Depends(get_normal_db)):
    """OAuth logoutで委任全体を失効する。存在は応答で漏らさない。"""
    params = await request.form(max_fields=5)
    if any(len(params.getlist(key)) != 1 for key in params):
        raise McpOAuthError("invalid_request")
    service.revoke_token(
        db, str(params.get("client_id", "")), str(params.get("token", ""))
    )
    return Response(status_code=200, headers={"Cache-Control": "no-store"})


@router.get(
    "/integrations/mcp/authorization-requests/{request_id}",
    response_model=McpConsentRead,
)
def consent(
    request_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """本人の現在の権限で接続候補を返す。"""
    return service.consent(db, user, request_id)


@router.post(
    "/integrations/mcp/authorization-requests/{request_id}/approve",
    response_model=McpRedirectRead,
)
def approve(
    request_id: UUID,
    data: McpConsentCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cookie/CSRFで保護された同意だけを受け付ける。"""
    return McpRedirectRead(
        redirect_url=service.approve(db, user, request_id, data.project_ids)
    )


@router.post(
    "/integrations/mcp/authorization-requests/{request_id}/deny",
    response_model=McpRedirectRead,
)
def deny(
    request_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """本人が拒否した要求を閉じる。"""
    return McpRedirectRead(redirect_url=service.deny(db, user, request_id))


@router.get("/integrations/mcp/connections", response_model=list[McpConnectionRead])
def connections(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """秘密を含めず本人の接続だけを返す。"""
    service.validate_browser(db, user)
    return service.repository.list_connections(db, user.id)


@router.delete("/integrations/mcp/connections/{connection_id}", status_code=204)
def revoke_connection(
    connection_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """アカウント画面から本人が接続を取り消す。"""
    service.revoke(db, user, connection_id)
