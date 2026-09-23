"""API routers."""

from fastapi import FastAPI

from app.routers import (
    auth,
    drafts,
    health,
    projects,
    requirements,
    tasks,
    test_designs,
    users,
)


def register_routers(app: FastAPI) -> None:
    """アプリケーションのルーターを登録する。

    Args:
        app: ルーターを登録するFastAPIアプリケーション。
    """
    app.include_router(auth.router)
    app.include_router(drafts.router)
    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(requirements.router)
    app.include_router(tasks.router)
    app.include_router(test_designs.router)
    app.include_router(users.router)
