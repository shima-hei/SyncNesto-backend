"""既存Cron秘密で認可した通知キューの回収。"""

import hmac

from fastapi import APIRouter, Request, Response

from app.core.config import settings
from app.core.exceptions import ForbiddenError
from app.services.approval_mail_dispatch import dispatch_pending_mail

router = APIRouter(tags=["internal"])


@router.get("/internal/approval-mail/deliver", include_in_schema=False)
def deliver_approval_mail(request: Request, response: Response):
    expected = "Bearer " + settings.demo_cron_secret
    if len(settings.demo_cron_secret) < 32 or not hmac.compare_digest(
        request.headers.get("authorization", "").encode(), expected.encode()
    ):
        raise ForbiddenError()
    response.headers["Cache-Control"] = "private, no-store"
    result = dispatch_pending_mail(limit=50, budget_seconds=40)
    if result.get("failed"):
        response.status_code = 503
    return result
