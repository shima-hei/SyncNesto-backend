"""
アプリケーションのエントリーポイント。
FastAPIアプリの生成、ルーティングの登録を行う
"""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.mcp import require_mcp_enabled
from app.core.middleware import register_middleware
from app.routers import register_routers


def create_app() -> FastAPI:
    """FastAPIアプリを生成し、ルーティングを登録する。

    Returns:
        初期設定済みのFastAPIアプリケーション。
    """
    configure_logging()
    settings.validate_production()
    production = settings.is_public_environment
    mcp_app = None
    if settings.mcp_enabled:
        require_mcp_enabled()
        if settings.mcp_resource_url == settings.mcp_issuer_url.rstrip("/") + "/mcp":
            from app.mcp import create_embedded_mcp

            mcp_app = create_embedded_mcp()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Mounted SDKのlifespanも親アプリから開始・終了する。"""
        if mcp_app is None:
            yield
        else:
            async with mcp_app.router.lifespan_context(mcp_app):
                yield

    fastapi_app = FastAPI(
        title=settings.app_name,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
        lifespan=lifespan,
    )

    register_middleware(fastapi_app)
    register_exception_handlers(fastapi_app)
    register_routers(fastapi_app)
    if mcp_app is not None:
        # 既存APIを先に解決し、SDKは/mcpとresource metadataだけを提供する。
        fastapi_app.mount("/", mcp_app)
    return fastapi_app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
