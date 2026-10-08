"""デモ開始と本人のリセット、およびCron専用回収のHTTP境界。"""

import hmac

from fastapi import APIRouter, Cookie, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.auth_cookie import set_auth_cookie
from app.core.config import settings
from app.core.csrf import generate_csrf_token, set_csrf_cookie
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.ingress import trusted_client_ip
from app.db.session import get_db, get_demo_db
from app.models.user import User
from app.schemas.demo import DemoCleanupResult, DemoStatus
from app.services.demo import IDLE_MINUTES, DemoService

router = APIRouter(tags=["demo"])
service = DemoService()


@router.get("/demo/csrf", status_code=204)
def demo_csrf(response: Response) -> None:
    """匿名開始でもdouble-submit tokenを要求する。"""
    if not settings.demo_mode:
        raise NotFoundError()
    set_csrf_cookie(response, generate_csrf_token())
    response.headers["Cache-Control"] = "private, no-store"


def start_response(db: Session, request: Request, response: Response) -> DemoStatus:
    """原子的な発行後にだけCookieを置き換える。"""
    if request.headers.get("origin") != settings.frontend_public_url.rstrip("/"):
        raise ForbiddenError()
    demo, token = service.start(db, trusted_client_ip(request))
    set_auth_cookie(response, token, IDLE_MINUTES * 60)
    set_csrf_cookie(response, generate_csrf_token())
    response.headers["Cache-Control"] = "private, no-store"
    return demo


@router.post("/demo/start", response_model=DemoStatus, status_code=201)
def start_demo(
    request: Request,
    response: Response,
    access_token: str | None = Cookie(default=None, alias=settings.auth_cookie_name),
    db: Session = Depends(get_demo_db),
) -> DemoStatus:
    """別タブや通常ログインの有効Cookieを匿名操作で置き換えない。"""
    if not settings.demo_mode:
        raise NotFoundError()
    if access_token:
        raise ConflictError("ログアウトしてからデモを開始してください")
    return start_response(db, request, response)


@router.get("/demo/status", response_model=DemoStatus)
def demo_status(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> DemoStatus:
    """本人に結び付いたデモだけを返す。"""
    if not (demo_id := db.info.get("demo_id")):
        raise NotFoundError()
    demo = service.repository.lock(db, demo_id)
    assert demo is not None
    return service.status(demo)


@router.post("/demo/reset", response_model=DemoStatus, status_code=201)
def reset_demo(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DemoStatus:
    """旧環境のアクセスを失効させ、回収後に新しい環境を発行する。"""
    if request.headers.get("origin") != settings.frontend_public_url.rstrip("/"):
        raise ForbiddenError()
    if not (demo_id := db.info.get("demo_id")):
        raise NotFoundError()
    service.revoke(db, demo_id, "reset")
    service.cleanup(demo_id)
    # 旧組織のORM取得条件を新規発行へ持ち込まない。
    db.info.clear()
    db.info["data_realm"] = "demo"
    return start_response(db, request, response)


@router.get(
    "/internal/demo/cleanup", response_model=DemoCleanupResult, include_in_schema=False
)
def cleanup_demo(request: Request) -> DemoCleanupResult:
    """Vercel Cronの専用秘密のみ受け付け、Cookie・BFF権限は使わない。"""
    if not settings.demo_database_url or not settings.demo_data_isolated:
        raise NotFoundError()
    expected = f"Bearer {settings.demo_cron_secret}"
    if len(settings.demo_cron_secret) < 32 or not hmac.compare_digest(
        request.headers.get("authorization", "").encode(), expected.encode()
    ):
        raise ForbiddenError()
    return service.sweep()
