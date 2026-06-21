"""要件定義routerで共有するサービスとヘルパーを定義するモジュール。"""

from sqlalchemy.orm import Session

from app.models.requirement import RequirementDocument
from app.models.user import User
from app.presenters.requirement import collect_requirement_document_user_ids
from app.services.requirement import (
    RequirementChangeLogService,
    RequirementChildService,
    RequirementDocumentService,
    RequirementOpenIssueService,
    RequirementSectionService,
    RequirementService,
    RequirementTargetCommentService,
)
from app.services.storage import StorageService
from app.services.user import UserService

document_service = RequirementDocumentService()
section_service = RequirementSectionService()
requirement_service = RequirementService()
open_issue_service = RequirementOpenIssueService()
change_log_service = RequirementChangeLogService()
target_comment_service = RequirementTargetCommentService()
requirement_child_service = RequirementChildService()
storage_service = StorageService()
user_service = UserService()


def get_requirement_document_users_by_id(
    db: Session,
    documents: list[RequirementDocument],
) -> dict[int, User]:
    """要件定義書レスポンスに必要な担当者ユーザーを取得する。

    Args:
        db: DBセッション。
        documents: 担当者ユーザーを解決する要件定義書一覧。

    Returns:
        ユーザーIDをキーにしたユーザー辞書。
    """
    user_ids = collect_requirement_document_user_ids(documents)
    users = user_service.list_users_by_ids(db, user_ids)
    return {user.id: user for user in users}
