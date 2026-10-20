"""招待管理と、所属を要求しない限定的な受信者操作。"""

from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.core.auth import get_authenticated_user, get_current_user
from app.core.config import settings
from app.core.exceptions import (
    ForbiddenError,
    MfaRequiredError,
    PasswordChangeRequiredError,
    UnauthorizedError,
)
from app.core.ingress import trusted_client_ip
from app.core.tenant import require_project_management
from app.db.session import get_db
from app.models.review_account import ReviewAccountGrant
from app.models.user import User
from app.schemas.guest_invitation import (
    InvitationCreate,
    InvitationEvents,
    InvitationOperation,
    InvitationPage,
    InvitationRead,
    InvitationRecipientRead,
    InvitationRegister,
    InvitationRevoke,
    InvitationToken,
    InvitationVersion,
)
from app.services.guest_invitation import GuestInvitationService
from app.services.request_limit import consume_email_request_budget, consume_mfa_budget


class InvitationRoute(APIRoute):
    """新しい入口の422へ確認secretやパスワードを反射させない。"""

    def get_route_handler(self):
        """既存APIの検証形式を変えず、新経路の入力値だけを除く。"""
        handler = super().get_route_handler()

        async def sanitized(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": [
                            {key: error[key] for key in ("type", "loc", "msg")}
                            for error in exc.errors()
                        ]
                    },
                )

        return sanitized


router = APIRouter(
    prefix="/projects/{project_id}/guest-invitations",
    tags=["guest-invitations"],
    route_class=InvitationRoute,
)
recipient_router = APIRouter(
    prefix="/guest-invitations",
    tags=["guest-invitation-recipient"],
    route_class=InvitationRoute,
)
service = GuestInvitationService()
Db = Annotated[Session, Depends(get_db)]
Manager = Annotated[User, Depends(require_project_management("project:invite_member"))]
Remover = Annotated[User, Depends(require_project_management("project:remove_member"))]
Page = Annotated[int, Query(ge=1)]
Size = Annotated[int, Query(ge=1, le=100)]
COOKIE = "guest_invitation_check"


def same_origin(request: Request):
    """匿名のメール確認/登録にも設定済みFrontendのOriginを必須にする。"""
    url = urlsplit(settings.frontend_public_url)
    if request.headers.get("origin") != f"{url.scheme}://{url.netloc}":
        raise ForbiddenError()


def optional_user(request: Request, response: Response, db: Db) -> User | None:
    """未認証を案内状態に変換するが、審査/デモ/MCPを通常登録へ混ぜない。"""
    if db.info.get("data_realm") == "demo" or db.info.get("mcp_connection_id"):
        raise ForbiddenError()
    try:
        user = get_authenticated_user(
            response,
            request,
            request.cookies.get(settings.auth_cookie_name),
            request.headers.get("authorization"),
            db,
        )
        if db.get(ReviewAccountGrant, user.id):
            raise ForbiddenError()
        return get_current_user(user, db)
    except (UnauthorizedError, MfaRequiredError, PasswordChangeRequiredError):
        return None


OptionalUser = Annotated[User | None, Depends(optional_user)]


@router.get("", response_model=InvitationPage)
def invitation_list(
    project_id: int, db: Db, user: Manager, page: Page = 1, page_size: Size = 20
):
    """Project内の招待を表示する。"""
    return service.listing(db, user, project_id, page, page_size)


@router.post("", response_model=InvitationRead, status_code=201)
def invitation_create(
    project_id: int, data: InvitationCreate, db: Db, user: Manager, request: Request
):
    """明示したダミー宛先への招待を作り、送信受付結果を返す。"""
    consume_email_request_budget(str(data.email), trusted_client_ip(request))
    row, token = service.prepare(db, user, project_id, data)
    return service.send(db, user, project_id, row.id, token, data) if token else row


@router.post("/{invitation_id}/resend", response_model=InvitationRead)
def invitation_resend(
    project_id: int,
    invitation_id: UUID,
    data: InvitationVersion,
    db: Db,
    user: Manager,
    request: Request,
):
    """旧リンクを無効にし、期限を延ばさず再送する。"""
    consume_email_request_budget(str(invitation_id), trusted_client_ip(request))
    row, token = service.prepare(db, user, project_id, data, invitation_id)
    return service.send(db, user, project_id, row.id, token, data) if token else row


@router.post("/{invitation_id}/revoke", response_model=InvitationOperation)
def invitation_revoke(
    project_id: int, invitation_id: UUID, data: InvitationRevoke, db: Db, user: Remover
):
    """受諾前の招待を取り消す。"""
    return service.revoke(db, user, project_id, invitation_id, data)


@router.get("/{invitation_id}/events", response_model=InvitationEvents)
def invitation_events(
    project_id: int,
    invitation_id: UUID,
    db: Db,
    user: Manager,
    page: Page = 1,
    page_size: Size = 20,
):
    """送信受付と受諾を混同しない履歴を返す。"""
    return service.events(db, user, project_id, invitation_id, page, page_size)


@recipient_router.post(
    "/{invitation_id}/inspect",
    response_model=InvitationRecipientRead,
    dependencies=[Depends(same_origin)],
)
def invitation_inspect(
    invitation_id: UUID,
    data: InvitationToken,
    request: Request,
    response: Response,
    db: Db,
    user: OptionalUser,
):
    """リンクの生tokenを短期の別secretへ交換する。"""
    consume_mfa_budget(str(invitation_id), trusted_client_ip(request))
    row, proof, ttl = service.inspect(db, invitation_id, data.token)
    response.set_cookie(
        COOKIE,
        proof,
        max_age=ttl,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path=f"/api/guest-invitations/{invitation_id}",
    )
    return row


@recipient_router.get("/{invitation_id}", response_model=InvitationRecipientRead)
def invitation_read(invitation_id: UUID, request: Request, db: Db, user: OptionalUser):
    """確認Cookieと現在の本人で次の操作を確認する。"""
    return service.recipient(db, invitation_id, request.cookies.get(COOKIE), user)


@recipient_router.post(
    "/{invitation_id}/register",
    response_model=InvitationRecipientRead,
    dependencies=[Depends(same_origin)],
)
def invitation_register(
    invitation_id: UUID,
    data: InvitationRegister,
    request: Request,
    db: Db,
    user: OptionalUser,
):
    """招待メールを確認した未登録本人だけが初回設定する。"""
    consume_mfa_budget(str(invitation_id), trusted_client_ip(request))
    return service.register(db, invitation_id, request.cookies.get(COOKIE), data, user)


@recipient_router.post(
    "/{invitation_id}/accept",
    response_model=InvitationOperation,
    dependencies=[Depends(same_origin)],
)
def invitation_accept(
    invitation_id: UUID,
    data: InvitationVersion,
    request: Request,
    db: Db,
    user: Annotated[User, Depends(get_current_user)],
):
    """通常追加認証を完了した宛先本人が参加する。"""
    return service.finish(db, invitation_id, request.cookies.get(COOKIE), data, user)


@recipient_router.post(
    "/{invitation_id}/decline",
    response_model=InvitationOperation,
    dependencies=[Depends(same_origin)],
)
def invitation_decline(
    invitation_id: UUID,
    data: InvitationVersion,
    request: Request,
    db: Db,
    user: OptionalUser,
):
    """メール確認者として辞退し、アカウント作成を強制しない。"""
    return service.finish(
        db, invitation_id, request.cookies.get(COOKIE), data, user, decline=True
    )
