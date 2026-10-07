"""組織管理と本人の組織選択API。"""

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_system_permission
from app.core.logging import client_ip_context
from app.core.tenant import get_current_tenant, require_tenant_admin
from app.db.session import get_db
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.account_action import AccountActionMessage, EmailChangeRequest
from app.schemas.tenant import (
    TenantChoice,
    TenantCreate,
    TenantIssue,
    TenantIssued,
    TenantMemberAdd,
    TenantMemberRead,
    TenantMemberUpdate,
    TenantRead,
    TenantUpdate,
    TenantUserCreate,
    TenantUserCreated,
    TenantWelcomeRequest,
)
from app.services.account_action import AccountActionService
from app.services.request_limit import consume_email_request_budget
from app.services.tenant import TenantService
from app.services.tenant_issuance import TenantIssuanceService

router = APIRouter(prefix="/tenants", tags=["tenants"])
service = TenantService()
account_action_service = AccountActionService()


@router.get("", response_model=list[TenantChoice])
def list_my_tenants(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """本人の有効な組織を一覧する。"""
    return service.choices(db, user.id)


@router.get("/management", response_model=list[TenantRead])
def list_managed_tenants(
    user: User = Depends(require_system_permission("tenant:manage")),
    db: Session = Depends(get_db),
):
    """運営者向けの組織メタデータ一覧。"""
    return service.repository.list_tenants(db)


@router.post("", response_model=TenantRead, status_code=201)
def create_tenant(
    data: TenantCreate,
    user: User = Depends(require_system_permission("tenant:manage")),
    db: Session = Depends(get_db),
):
    """運営者が組織と初期Ownerを作る。"""
    return service.create(db, data, user.id)


@router.post("/issuance", response_model=TenantIssued, status_code=201)
def issue_tenant(
    data: TenantIssue,
    user: User = Depends(require_system_permission("tenant:manage")),
    db: Session = Depends(get_db),
) -> TenantIssued:
    """運営承認後に組織と初期Ownerを発行し、案内メールを送る。"""
    consume_email_request_budget(
        str(data.owner_email), client_ip_context.get() or "unknown"
    )
    return TenantIssuanceService().issue(db, data, user.id)


@router.post("/{tenant_id}/welcome-email", response_model=TenantIssued)
def resend_tenant_welcome(
    tenant_id: int,
    data: TenantWelcomeRequest,
    user: User = Depends(require_system_permission("tenant:manage")),
    db: Session = Depends(get_db),
) -> TenantIssued:
    """有効なOwnerへ案内を再送し、初回設定待ちの場合だけパスワードを再発行する。"""
    consume_email_request_budget(
        str(data.owner_email), client_ip_context.get() or "unknown"
    )
    return TenantIssuanceService().resend(db, tenant_id, str(data.owner_email), user.id)


@router.get("/current", response_model=TenantChoice)
def get_tenant(
    tenant: Tenant = Depends(get_current_tenant), db: Session = Depends(get_db)
):
    """検証済みの現在の組織を返す。"""
    return TenantChoice(
        **TenantRead.model_validate(tenant).model_dump(),
        role_key=db.info["tenant_role"],
    )


@router.patch("/current", response_model=TenantRead)
def update_tenant(
    data: TenantUpdate,
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
):
    """組織設定を更新する。"""
    return service.update(db, tenant.id, data, user.id)


@router.get("/current/members", response_model=list[TenantMemberRead])
def list_members(
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
):
    """組織内ユーザーを一覧する。"""
    return service.members(db, tenant.id)


@router.post("/current/members", response_model=TenantMemberRead, status_code=201)
def add_member(
    data: TenantMemberAdd,
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
):
    """登録済みIdentityをメール完全一致で追加する。"""
    return service.add_member(db, tenant.id, data, user.id)


@router.patch("/current/members/{user_id}", response_model=TenantMemberRead)
def update_member(
    user_id: int,
    data: TenantMemberUpdate,
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
):
    """所属・組織内プロフィールを変更する。"""
    return service.change_member(db, tenant.id, user_id, data, user.id)


@router.delete("/current/members/{user_id}", status_code=204)
def remove_member(
    user_id: int,
    version: int = Query(...),
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
):
    """所属を削除し、同じ組織内のProject所属も無効にする。"""
    service.change_member(
        db,
        tenant.id,
        user_id,
        TenantMemberUpdate(version=version),
        user.id,
        remove=True,
    )
    return Response(status_code=204)


@router.post("/current/users", response_model=TenantUserCreated, status_code=201)
def create_user(
    data: TenantUserCreate,
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
):
    """組織内の新規ユーザーを登録する。"""
    return service.create_user(db, tenant.id, data, user.id)


@router.post(
    "/current/members/{user_id}/password-reset",
    response_model=AccountActionMessage,
    status_code=202,
)
def request_member_password_reset(
    user_id: int,
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
):
    """有効な組織内ユーザーの登録先へ、本人が選ぶ再設定リンクを送信する。"""
    consume_email_request_budget(
        f"user:{user_id}", client_ip_context.get() or "unknown"
    )
    account_action_service.request_password_reset(
        db, user_id=user_id, actor_id=user.id, tenant_id=tenant.id
    )
    return AccountActionMessage(
        message="デモのためメールは送信しません"
        if db.info.get("demo_id")
        else "登録済みメールアドレスに再設定メールを送信しました"
    )


@router.post(
    "/current/members/{user_id}/email-change",
    response_model=AccountActionMessage,
    status_code=202,
)
def request_member_email_change(
    user_id: int,
    data: EmailChangeRequest,
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
):
    """管理者が申請し、旧メール承認・新メール確認の両方を本人に求める。"""
    consume_email_request_budget(
        f"user:{user_id}", client_ip_context.get() or "unknown"
    )
    account_action_service.request_email_change(
        db,
        user_id=user_id,
        actor_id=user.id,
        tenant_id=tenant.id,
        new_email=str(data.new_email),
    )
    return AccountActionMessage(
        message="デモのためメールは送信しません"
        if db.info.get("demo_id")
        else "現在のメールアドレスに承認メールを送信しました"
    )
