"""
アプリケーションのエントリーポイント。
FastAPIアプリの生成、ルーティングの登録を行う
"""

from fastapi import FastAPI
import uvicorn

from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.middleware import register_middleware
from app.routers import health


def create_app() -> FastAPI:
    """FastAPIアプリを生成し、ルーティングを登録する。

    Returns:
        初期設定済みのFastAPIアプリケーション。
    """
    fastapi_app = FastAPI(title=settings.app_name)

    register_middleware(fastapi_app)
    register_exception_handlers(fastapi_app)
    fastapi_app.include_router(health.router)
    return fastapi_app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
