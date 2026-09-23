"""タスク管理Repositoryの互換インポートを提供するモジュール。"""

from app.repositories.task_board import BoardColumnRepository, BoardRepository
from app.repositories.task_change_log import TaskChangeLogRepository
from app.repositories.task_comment import TaskCommentRepository
from app.repositories.task_dependency import TaskDependencyRepository
from app.repositories.task_item import TaskRepository
from app.repositories.task_milestone import MilestoneRepository
from app.repositories.task_requirement import (
    RequirementTaskRelationRepository,
    TaskRequirementLookupRepository,
)

__all__ = [
    "BoardColumnRepository",
    "BoardRepository",
    "MilestoneRepository",
    "RequirementTaskRelationRepository",
    "TaskChangeLogRepository",
    "TaskCommentRepository",
    "TaskDependencyRepository",
    "TaskRepository",
    "TaskRequirementLookupRepository",
]
