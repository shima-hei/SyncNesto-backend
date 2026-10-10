"""通常Identity専用の追加認証API。限定Cookieを業務認証へ流用しない。"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_system_permission
from app.core.auth_cookie import set_auth_cookie
from app.core.config import settings
from app.core.csrf import generate_csrf_token, set_csrf_cookie
from app.core.exceptions import ForbiddenError
from app.core.ingress import trusted_client_ip
from app.core.mfa import (
    delete_flow_cookie,
    digest,
    require_recent,
    require_verified,
    set_flow_cookie,
)
from app.db.session import get_db, get_normal_db, verified_request_realm
from app.models.mfa import MfaFlow
from app.models.session import UserSession
from app.models.user import User
from app.schemas.mfa import (
    MfaAuthenticationOptions,
    MfaCode,
    MfaFlowRead,
    MfaName,
    MfaPasskeyResponse,
    MfaPassword,
    MfaRecover,
    MfaRecoveryApproval,
    MfaRegistrationOptions,
    MfaStatus,
    MfaTotpSetup,
)
from app.services.mfa import MfaService
from app.services.request_limit import consume_mfa_budget

service = MfaService()


def normal_request(request: Request, response: Response) -> None:
    """デモから通常認証情報への操作を拒否し、秘密のキャッシュを防ぐ。"""
    if verified_request_realm(request) == "demo":
        raise ForbiddenError()
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/auth", tags=["mfa"], dependencies=[Depends(normal_request)])
operator_router = APIRouter(
    prefix="/users", tags=["mfa"], dependencies=[Depends(normal_request)]
)


def limited_context(
    request: Request, db: Session = Depends(get_normal_db)
) -> tuple[MfaFlow, User]:
    """有効な限定Cookieを本人と目的に照合する。"""
    return service.flow(db, request.cookies.get(settings.mfa_cookie_name))


def consume_attempt(request: Request, user: User) -> None:
    """Cookieを更新しても共有される試行上限を先に確認する。"""
    consume_mfa_budget(f"user:{user.id}", trusted_client_ip(request))


def normal_session(db: Session, user: User) -> UserSession:
    """本人の検証済み通常セッションを取り出す。"""
    session = db.info.get("auth_session")
    if not isinstance(session, UserSession) or db.info.get("data_realm") == "demo":
        raise ForbiddenError()
    require_verified(user, session)
    return session


def complete(response: Response, result: tuple[MfaFlowRead, str | None]) -> MfaFlowRead:
    """通常認証の完了時だけaccess Cookieを発行する。"""
    state, token = result
    if state.next_step == "authenticated":
        delete_flow_cookie(response)
    if token:
        set_auth_cookie(
            response, token, max_age_seconds=settings.session_idle_timeout_minutes * 60
        )
        set_csrf_cookie(response, generate_csrf_token())
    return state


@router.get("/flow", response_model=MfaFlowRead)
def read_mfa_flow(
    context: tuple[MfaFlow, User] = Depends(limited_context),
    db: Session = Depends(get_normal_db),
) -> MfaFlowRead:
    """プロフィールや組織を含めず、次に必要な認証だけを返す。"""
    return service.read(db, *context)


@router.post("/mfa/totp/setup", response_model=MfaTotpSetup)
def setup_totp(
    data: MfaName,
    context: tuple[MfaFlow, User] = Depends(limited_context),
    db: Session = Depends(get_normal_db),
) -> MfaTotpSetup:
    """別の認証アプリとして独立した秘密を発行する。"""
    return service.begin_totp(db, *context, data.name)


@router.post("/mfa/verify-code", response_model=MfaFlowRead)
def verify_mfa_code(
    data: MfaCode,
    request: Request,
    response: Response,
    context: tuple[MfaFlow, User] = Depends(limited_context),
    db: Session = Depends(get_normal_db),
) -> MfaFlowRead:
    """TOTPまたは復旧コードを一度だけ検証する。"""
    consume_attempt(request, context[1])
    return complete(
        response, service.verify_code(db, *context, data.code, recovery=data.recovery)
    )


@router.post("/mfa/passkeys/register-options", response_model=MfaRegistrationOptions)
def register_passkey_options(
    data: MfaName,
    context: tuple[MfaFlow, User] = Depends(limited_context),
    db: Session = Depends(get_normal_db),
) -> dict:
    """登録用challengeを固定のRPと本人へ結び付ける。"""
    return service.passkey_options(db, *context, name=data.name)


@router.post(
    "/mfa/passkeys/authenticate-options", response_model=MfaAuthenticationOptions
)
def authenticate_passkey_options(
    context: tuple[MfaFlow, User] = Depends(limited_context),
    db: Session = Depends(get_normal_db),
) -> dict:
    """本人の登録済みPasskeyだけに認証を要求する。"""
    return service.passkey_options(db, *context)


@router.post("/mfa/passkeys/verify", response_model=MfaFlowRead)
def verify_mfa_passkey(
    data: MfaPasskeyResponse,
    request: Request,
    response: Response,
    context: tuple[MfaFlow, User] = Depends(limited_context),
    db: Session = Depends(get_normal_db),
) -> MfaFlowRead:
    """署名・origin・RP・UVと一度限りのchallengeを検証する。"""
    consume_attempt(request, context[1])
    return complete(response, service.verify_passkey(db, *context, data.credential))


@router.post("/mfa/finish", response_model=MfaFlowRead)
def finish_mfa(
    response: Response,
    context: tuple[MfaFlow, User] = Depends(limited_context),
    db: Session = Depends(get_normal_db),
) -> MfaFlowRead:
    """復旧コードの保存確認後に初回登録を完了する。"""
    return complete(response, service.finish(db, *context))


@router.get("/mfa", response_model=MfaStatus)
def read_mfa_status(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> MfaStatus:
    """本人の登録済み認証手段を返す。"""
    return service.status(db, user, normal_session(db, user))


@router.post("/mfa/manage", response_model=MfaFlowRead)
def start_mfa_management(
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MfaFlowRead:
    """直近認証済みの本人に追加登録を許可する。"""
    token, state = service.begin_management(db, user, normal_session(db, user))
    set_flow_cookie(response, token)
    return state


@router.post("/mfa/reauth", response_model=MfaFlowRead)
def start_mfa_reauthentication(
    data: MfaPassword,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MfaFlowRead:
    """パスワードと登録済み認証器を再確認する。"""
    consume_attempt(request, user)
    token, state = service.begin_management(
        db, user, normal_session(db, user), reauthenticate=True, password=data.password
    )
    set_flow_cookie(response, token)
    return state


@router.delete("/mfa/factors/{factor_id}", status_code=204)
def remove_mfa_factor(
    factor_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """最後の認証器を残し、削除後は全接続を失効する。"""
    service.remove(db, user, factor_id, normal_session(db, user))


@router.post("/mfa/cancel", status_code=204)
def cancel_mfa(
    request: Request, response: Response, db: Session = Depends(get_normal_db)
) -> None:
    """通常セッションを維持したまま限定状態だけを取り消す。"""
    token = request.cookies.get(settings.mfa_cookie_name)
    if token:
        service.repository.cancel(db, digest(token))
    delete_flow_cookie(response)


@router.patch("/mfa/factors/{factor_id}", status_code=204)
def rename_mfa_factor(
    factor_id: UUID,
    data: MfaName,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """本人の認証器の表示名を変更する。"""
    service.rename(db, user, normal_session(db, user), factor_id, data.name)


@router.post("/mfa/recovery-codes", response_model=list[str])
def regenerate_mfa_codes(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[str]:
    """直近認証後に復旧コードを一度だけ表示する。"""
    return service.regenerate_codes(db, user, normal_session(db, user))


@router.post("/mfa/recover", response_model=MfaFlowRead)
def recover_mfa(
    data: MfaRecover,
    request: Request,
    response: Response,
    db: Session = Depends(get_normal_db),
) -> MfaFlowRead:
    """運営者の承認済みリンクとパスワードを検証する。"""
    consume_mfa_budget(f"recovery:{digest(data.token)}", trusted_client_ip(request))
    token, state = service.recover(db, data.token, data.password)
    set_flow_cookie(response, token)
    set_csrf_cookie(response, generate_csrf_token())
    return state


@operator_router.post("/{user_id}/mfa-recovery", status_code=204)
def approve_mfa_recovery(
    user_id: int,
    data: MfaRecoveryApproval,
    actor: User = Depends(require_system_permission("user:mfa_recover")),
    db: Session = Depends(get_db),
) -> None:
    """別の運営者が本人確認の根拠を記録して再登録を支援する。"""
    session = normal_session(db, actor)
    require_recent(session)
    if session.mfa_method not in {"totp", "passkey"}:
        raise ForbiddenError()
    service.approve_recovery(db, actor, user_id, data.verification_method, data.reason)
