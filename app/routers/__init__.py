"""API routers."""

from fastapi import FastAPI

from app.routers import auth, drafts, health, projects, requirements, tasks, users


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
    app.include_router(users.router)
