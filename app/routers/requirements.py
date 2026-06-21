"""要件定義APIの集約routerを定義するモジュール。"""

from fastapi import APIRouter

from app.routers import (
    requirement_change_logs,
    requirement_children,
    requirement_documents,
    requirement_items,
    requirement_open_issues,
    requirement_sections,
    requirement_target_comments,
)

router = APIRouter()
router.include_router(requirement_documents.router)
router.include_router(requirement_sections.router)
router.include_router(requirement_items.router)
router.include_router(requirement_open_issues.router)
router.include_router(requirement_target_comments.router)
router.include_router(requirement_children.router)
router.include_router(requirement_change_logs.router)
