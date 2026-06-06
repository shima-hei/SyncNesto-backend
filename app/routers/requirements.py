"""要件定義APIの集約routerを定義するモジュール。"""

from fastapi import APIRouter

from app.routers import requirement_children, requirement_documents, requirement_items

router = APIRouter()
router.include_router(requirement_documents.router)
router.include_router(requirement_items.router)
router.include_router(requirement_children.router)
