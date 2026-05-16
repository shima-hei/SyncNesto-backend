"""API routers."""

from fastapi import FastAPI

from app.routers import auth, health, projects, users


def register_routers(app: FastAPI) -> None:
    """アプリケーションのルーターを登録する。

    Args:
        app: ルーターを登録するFastAPIアプリケーション。
    """
    app.include_router(auth.router)
    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(users.router)
