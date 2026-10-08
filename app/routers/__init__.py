"""API routers."""

from fastapi import Depends, FastAPI

from app.core.tenant import get_current_tenant
from app.routers import (
    account_actions,
    auth,
    demo,
    documents,
    drafts,
    health,
    home,
    notifications,
    projects,
    requirements,
    search,
    tasks,
    tenants,
    test_collaboration,
    test_designs,
    test_issues,
    users,
)


def register_routers(app: FastAPI) -> None:
    """アプリケーションのルーターを登録する。

    Args:
        app: ルーターを登録するFastAPIアプリケーション。
    """
    app.include_router(auth.router)
    app.include_router(demo.router)
    app.include_router(account_actions.router)
    app.include_router(drafts.router, dependencies=[Depends(get_current_tenant)])
    app.include_router(health.router)
    for router in (
        home.router,
        search.router,
        notifications.router,
        projects.router,
        documents.router,
        requirements.router,
        tasks.router,
        test_designs.router,
        test_collaboration.router,
        test_issues.router,
    ):
        app.include_router(router, dependencies=[Depends(get_current_tenant)])
    app.include_router(users.router)
    app.include_router(tenants.router)
