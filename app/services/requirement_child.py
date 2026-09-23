"""要件詳細、リンク、コメント、レビューサービスを定義するモジュール。"""

from app.services.requirement_comment import RequirementCommentService
from app.services.requirement_detail import RequirementDetailService
from app.services.requirement_link import RequirementLinkService
from app.services.requirement_relation import RequirementRelationService
from app.services.requirement_review import RequirementReviewService
from app.services.requirement_summary import (
    RequirementSummary,
    RequirementSummaryService,
)


class RequirementChildService(
    RequirementSummaryService,
    RequirementDetailService,
    RequirementLinkService,
    RequirementRelationService,
    RequirementCommentService,
    RequirementReviewService,
):
    """要件詳細、リンク、関連、コメント、レビューServiceの互換Facade。"""


__all__ = ["RequirementChildService", "RequirementSummary"]
