"""タスク系Routerで共有するServiceとPresenterを定義するモジュール。"""

from app.presenters.task import TaskPresenter
from app.services.task import TaskService
from app.services.task_board import TaskBoardService
from app.services.task_change_log import TaskChangeLogService
from app.services.task_comment import TaskCommentService
from app.services.task_dependency import TaskDependencyService
from app.services.task_milestone import TaskMilestoneService
from app.services.task_presentation import TaskPresentationDataService

task_service = TaskService()
task_comment_service = TaskCommentService(
    task_repository=task_service.task_repository,
    comment_repository=task_service.comment_repository,
    change_log_repository=task_service.change_log_repository,
)
task_change_log_service = TaskChangeLogService(
    task_repository=task_service.task_repository,
    change_log_repository=task_service.change_log_repository,
    comment_repository=task_service.comment_repository,
    requirement_lookup_repository=task_service.requirement_lookup_repository,
)
task_dependency_service = TaskDependencyService(
    task_repository=task_service.task_repository,
    dependency_repository=task_service.dependency_repository,
    change_log_repository=task_service.change_log_repository,
)
task_milestone_service = TaskMilestoneService(
    milestone_repository=task_service.milestone_repository,
    project_repository=task_service.project_repository,
    change_log_repository=task_service.change_log_repository,
)
task_presentation_data_service = TaskPresentationDataService(
    task_repository=task_service.task_repository,
    relation_repository=task_service.relation_repository,
    dependency_repository=task_service.dependency_repository,
)
task_board_service = TaskBoardService(
    task_repository=task_service.task_repository,
    project_repository=task_service.project_repository,
    change_log_repository=task_service.change_log_repository,
    presentation_data_service=task_presentation_data_service,
)
task_presenter = TaskPresenter()
