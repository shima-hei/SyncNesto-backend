"""要件定義Repositoryの互換インポートを提供するモジュール。"""

from app.repositories.requirement_approval import RequirementApprovalRepository
from app.repositories.requirement_change_log import RequirementChangeLogRepository
from app.repositories.requirement_child import (
    RequirementCommentRepository,
    RequirementDetailRepository,
    RequirementLinkRepository,
    RequirementRelationRepository,
    RequirementReviewRepository,
    RequirementRevisionRepository,
)
from app.repositories.requirement_document import RequirementDocumentRepository
from app.repositories.requirement_item import RequirementRepository
from app.repositories.requirement_open_issue import RequirementOpenIssueRepository
from app.repositories.requirement_section import RequirementSectionRepository
from app.repositories.requirement_target_comment import (
    RequirementTargetCommentRepository,
)

__all__ = [
    "RequirementApprovalRepository",
    "RequirementChangeLogRepository",
    "RequirementCommentRepository",
    "RequirementDetailRepository",
    "RequirementDocumentRepository",
    "RequirementLinkRepository",
    "RequirementOpenIssueRepository",
    "RequirementRelationRepository",
    "RequirementRepository",
    "RequirementReviewRepository",
    "RequirementRevisionRepository",
    "RequirementSectionRepository",
    "RequirementTargetCommentRepository",
]
