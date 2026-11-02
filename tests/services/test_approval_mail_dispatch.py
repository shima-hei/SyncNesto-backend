"""本番配送の起動順・上限・Cron認可を外部接続なしで検証する。"""

from unittest.mock import Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.exceptions import ForbiddenError
from app.core.ingress import IngressMiddleware
from app.routers import approval_mail_delivery
from app.routers.approval_mail_route import ApprovalMailRoute
from app.services import approval_mail_dispatch as dispatch

pytestmark = pytest.mark.no_db


def test_queue_is_bounded_and_unknown_is_not_retried_in_dispatch(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    worker = Mock()
    worker.run_once.side_effect = [{"unknown": 1}, {"accepted": 1}, {"idle": 1}]
    monkeypatch.setattr(dispatch, "ApprovalMailWorker", lambda _: worker)
    assert dispatch.dispatch_pending_mail(limit=2) == {"unknown": 1, "accepted": 1}
    assert worker.run_once.call_count == 2
    assert dispatch.dispatch_pending_mail() == {"idle": 1}
    worker.run_once.side_effect = RuntimeError("private provider response")
    assert dispatch.dispatch_pending_mail() == {"failed": 1}


def test_successful_save_dispatches_after_commit_and_preview_does_not(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    events = []
    monkeypatch.setattr(
        "app.routers.approval_mail_route.dispatch_pending_mail",
        lambda: events.append("delivery"),
    )
    router = APIRouter(route_class=ApprovalMailRoute)

    @router.post("/fixed-approvals")
    def save():
        events.append("commit")
        return {"saved": True}

    @router.post("/fixed-approvals/preview")
    def preview():
        return {"preview": True}

    @router.post("/fixed-approvals/failure")
    def failure():
        return JSONResponse({}, status_code=409)

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.post("/fixed-approvals").status_code == 200
        assert events == ["commit", "delivery"]
        assert client.post("/fixed-approvals/preview").status_code == 200
        assert client.post("/fixed-approvals/failure").status_code == 409
        assert events == ["commit", "delivery"]


def test_cron_requires_existing_secret_even_with_cookie_or_bff(monkeypatch):
    monkeypatch.setattr(settings, "demo_cron_secret", "dummy-cron-secret-" * 3)
    monkeypatch.setattr(settings, "bff_shared_secret", "dummy-bff")
    run = Mock(return_value={"idle": 1})
    monkeypatch.setattr(approval_mail_delivery, "dispatch_pending_mail", run)
    app = FastAPI()
    app.add_middleware(IngressMiddleware)
    app.add_exception_handler(
        ForbiddenError, lambda *_: JSONResponse({}, status_code=403)
    )
    app.include_router(approval_mail_delivery.router)
    with TestClient(app) as client:
        path = "/internal/approval-mail/deliver"
        assert (
            client.get(path, headers={"Cookie": "session=pretend"}).status_code == 403
        )
        assert (
            client.get(path, headers={"X-Syncnesto-BFF-Key": "dummy-bff"}).status_code
            == 403
        )
        run.assert_not_called()
        result = client.get(
            path, headers={"Authorization": "Bearer " + settings.demo_cron_secret}
        )
        assert result.status_code == 200 and result.json() == {"idle": 1}
        assert result.headers["cache-control"] == "private, no-store"
        assert path not in app.openapi()["paths"]
