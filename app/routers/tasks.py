"""タスク管理APIの集約ルーティングを定義するモジュール。"""

from fastapi import APIRouter

from app.routers import (
    task_boards,
    task_change_logs,
    task_comments,
    task_dependencies,
    task_items,
    task_milestones,
)

router = APIRouter(tags=["tasks"])
router.include_router(task_boards.router)
router.include_router(task_change_logs.router)
router.include_router(task_comments.router)
router.include_router(task_dependencies.router)
router.include_router(task_items.router)
router.include_router(task_milestones.router)
