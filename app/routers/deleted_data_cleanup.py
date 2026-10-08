"""通常データの回収は、専用秘密とサーバー設定だけで認可する。"""

import hmac
import logging

from fastapi import APIRouter, Request, Response

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.schemas.deleted_data_cleanup import DeletedDataCleanupResult
from app.services.deleted_data_cleanup import DeletedDataCleanupService

router = APIRouter(tags=["internal"])
logger = logging.getLogger(__name__)


@router.get(
    "/internal/trash/cleanup",
    response_model=DeletedDataCleanupResult,
    include_in_schema=False,
)
def cleanup_deleted_data(
    request: Request, response: Response
) -> DeletedDataCleanupResult:
    """Vercel CronのGETだけを受け付け、利用者のCookieを権限に使わない。"""
    if settings.demo_mode or settings.deleted_data_cleanup_mode == "disabled":
        raise NotFoundError()
    expected = f"Bearer {settings.demo_cron_secret}"
    if len(settings.demo_cron_secret) < 32 or not hmac.compare_digest(
        request.headers.get("authorization", "").encode(), expected.encode()
    ):
        raise ForbiddenError()
    response.headers["Cache-Control"] = "private, no-store"
    try:
        result = DeletedDataCleanupService().run()
    except Exception:
        logger.error("Scheduled cleanup failed; review configuration and retry")
        response.status_code = 503
        return DeletedDataCleanupResult(
            mode="execute"
            if settings.deleted_data_cleanup_mode == "execute"
            else "dry_run",
            failed_count=1,
        )
    if result.failed_count:
        response.status_code = 503
    return result
