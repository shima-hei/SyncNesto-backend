"""SQLAlchemy models."""

from app.models.audit_log import AuditLog
from app.models.login_attempt import LoginAttempt
from app.models.project import Project, ProjectMember
from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.requirement import (
    Requirement,
    RequirementApproval,
    RequirementChangeLog,
    RequirementComment,
    RequirementDetail,
    RequirementDocument,
    RequirementLink,
    RequirementOpenIssue,
    RequirementRelation,
    RequirementReview,
    RequirementRevision,
    RequirementSection,
    RequirementTargetComment,
)
from app.models.session import UserSession
from app.models.task import (
    Board,
    BoardColumn,
    Milestone,
    RequirementTaskRelation,
    Task,
    TaskChangeLog,
    TaskComment,
    TaskDependency,
)
from app.models.user import User

__all__ = [
    "AuditLog",
    "Board",
    "BoardColumn",
    "LoginAttempt",
    "Milestone",
    "Permission",
    "Project",
    "ProjectMember",
    "Requirement",
    "RequirementApproval",
    "RequirementChangeLog",
    "RequirementComment",
    "RequirementDetail",
    "RequirementDocument",
    "RequirementLink",
    "RequirementOpenIssue",
    "RequirementRelation",
    "RequirementReview",
    "RequirementRevision",
    "RequirementSection",
    "RequirementTargetComment",
    "RequirementTaskRelation",
    "Role",
    "RolePermission",
    "Task",
    "TaskChangeLog",
    "TaskComment",
    "TaskDependency",
    "User",
    "UserRole",
    "UserSession",
]
