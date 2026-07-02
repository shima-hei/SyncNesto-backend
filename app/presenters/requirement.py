"""要件定義関連レスポンスのPresenterを定義するモジュール。"""

from typing import cast

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
    RequirementTargetComment,
)
from app.models.user import User
from app.presenters.user import get_user_type
from app.schemas.change_log import (
    ChangeLogUserRead,
    ChangeLogValue,
    RequirementChangeLogActionCode,
    RequirementChangeLogFieldName,
    RequirementChangeLogTargetTypeCode,
)
from app.schemas.requirement import (
    RequirementApprovalListResponse,
    RequirementApprovalRead,
    RequirementChangeLogListResponse,
    RequirementChangeLogRead,
    RequirementCommentRead,
    RequirementDetailRead,
    RequirementDocumentExportRead,
    RequirementDocumentListResponse,
    RequirementDocumentRead,
    RequirementLinkRead,
    RequirementListResponse,
    RequirementOpenIssueListResponse,
    RequirementOpenIssueRead,
    RequirementRead,
    RequirementRelationRead,
    RequirementReviewRead,
    RequirementRevisionRead,
    RequirementSummaryRead,
    RequirementTargetCommentRead,
)
from app.schemas.user import UserSummary
from app.services.requirement_change_log import REQUIREMENT_CHANGE_LOG_FORMATTER
from app.services.requirement_export import RequirementDocumentExportResult
from app.services.storage import StorageService


def collect_requirement_document_user_ids(
    documents: list[RequirementDocument],
) -> list[int]:
    """要件定義書一覧から担当者ユーザーID一覧を取得する。

    Args:
        documents: 要件定義書一覧。

    Returns:
        重複を除いた担当者ユーザーID一覧。
    """
    user_ids: list[int] = []
    for document in documents:
        for user_id in (
            document.author_id,
            document.reviewer_id,
            document.approver_id,
        ):
            if user_id is not None:
                user_ids.append(user_id)

    return list(dict.fromkeys(user_ids))


def build_requirement_document_response(
    document: RequirementDocument,
    users_by_id: dict[int, User] | None = None,
    storage_service: StorageService | None = None,
) -> RequirementDocumentRead:
    """要件定義書レスポンスを組み立てる。

    Args:
        document: レスポンスへ変換する要件定義書。
        users_by_id: 担当者ユーザーIDをkeyにしたユーザー辞書。
        storage_service: avatar_url生成に使用するストレージサービス。

    Returns:
        要件定義書読み取りレスポンス。
    """
    users = users_by_id or {}

    def get_user_summary(user_id: int | None) -> UserSummary | None:
        """担当者IDから軽量ユーザー情報を作成する。"""
        if user_id is None or storage_service is None:
            return None

        user = users.get(user_id)
        if user is None:
            return None

        return UserSummary(
            id=user.id,
            email=user.email,
            name=user.name,
            avatar_url=storage_service.generate_presigned_url(user.avatar_key),
            user_type=get_user_type(user),
            is_active=user.is_active,
        )

    return RequirementDocumentRead.model_validate(document).model_copy(
        update={
            "author": get_user_summary(document.author_id),
            "reviewer": get_user_summary(document.reviewer_id),
            "approver": get_user_summary(document.approver_id),
        }
    )


def build_requirement_document_responses(
    documents: list[RequirementDocument],
    users_by_id: dict[int, User],
    storage_service: StorageService,
) -> list[RequirementDocumentRead]:
    """要件定義書一覧レスポンスを組み立てる。

    Args:
        documents: レスポンスへ変換する要件定義書一覧。
        users_by_id: 担当者ユーザーIDをkeyにしたユーザー辞書。
        storage_service: avatar_url生成に使用するストレージサービス。

    Returns:
        要件定義書読み取りレスポンス一覧。
    """
    return [
        build_requirement_document_response(document, users_by_id, storage_service)
        for document in documents
    ]


def build_requirement_document_list_response(
    documents: list[RequirementDocument],
    *,
    users_by_id: dict[int, User],
    storage_service: StorageService,
    total: int,
    page: int,
    page_size: int,
) -> RequirementDocumentListResponse:
    """要件定義書一覧レスポンスを組み立てる。

    Args:
        documents: レスポンスへ変換する要件定義書一覧。
        users_by_id: 担当者ユーザーIDをkeyにしたユーザー辞書。
        storage_service: avatar_url生成に使用するストレージサービス。
        total: 全件数。
        page: ページ番号。
        page_size: 1ページあたりの件数。

    Returns:
        要件定義書一覧レスポンス。
    """
    return RequirementDocumentListResponse(
        items=build_requirement_document_responses(
            documents,
            users_by_id,
            storage_service,
        ),
        total=total,
        page=page,
        page_size=page_size,
    )


def build_requirement_document_export_response(
    export_result: RequirementDocumentExportResult,
) -> RequirementDocumentExportRead:
    """要件定義書出力レスポンスを組み立てる。

    Args:
        export_result: 要件定義書出力処理の結果値。

    Returns:
        要件定義書出力レスポンス。
    """
    return RequirementDocumentExportRead(
        format=export_result.format,
        content=export_result.content,
    )


def build_requirement_approval_response(
    approval: RequirementApproval,
) -> RequirementApprovalRead:
    """要件定義承認レスポンスを組み立てる。

    Args:
        approval: レスポンスへ変換する要件定義承認。

    Returns:
        要件定義承認読み取りレスポンス。
    """
    return RequirementApprovalRead.model_validate(approval)


def build_requirement_approval_list_response(
    approvals: list[RequirementApproval],
    *,
    total: int,
    page: int,
    page_size: int,
) -> RequirementApprovalListResponse:
    """要件定義承認一覧レスポンスを組み立てる。

    Args:
        approvals: レスポンスへ変換する要件定義承認一覧。
        total: 全件数。
        page: ページ番号。
        page_size: 1ページあたりの件数。

    Returns:
        要件定義承認一覧レスポンス。
    """
    return RequirementApprovalListResponse(
        items=[build_requirement_approval_response(approval) for approval in approvals],
        total=total,
        page=page,
        page_size=page_size,
    )


def build_requirement_response(requirement: Requirement) -> RequirementRead:
    """要件レスポンスを組み立てる。

    Args:
        requirement: レスポンスへ変換する要件。

    Returns:
        要件読み取りレスポンス。
    """
    return RequirementRead.model_validate(requirement)


def build_requirement_list_response(
    requirements: list[Requirement],
    *,
    total: int,
    page: int,
    page_size: int,
) -> RequirementListResponse:
    """要件一覧レスポンスを組み立てる。

    Args:
        requirements: レスポンスへ変換する要件一覧。
        total: 全件数。
        page: ページ番号。
        page_size: 1ページあたりの件数。

    Returns:
        要件一覧レスポンス。
    """
    return RequirementListResponse(
        items=[build_requirement_response(requirement) for requirement in requirements],
        total=total,
        page=page,
        page_size=page_size,
    )


def build_requirement_open_issue_response(
    issue: RequirementOpenIssue,
) -> RequirementOpenIssueRead:
    """未決事項レスポンスを組み立てる。

    Args:
        issue: レスポンスへ変換する未決事項。

    Returns:
        未決事項読み取りレスポンス。
    """
    return RequirementOpenIssueRead.model_validate(issue)


def build_requirement_open_issue_list_response(
    issues: list[RequirementOpenIssue],
    *,
    total: int,
    page: int,
    page_size: int,
) -> RequirementOpenIssueListResponse:
    """未決事項一覧レスポンスを組み立てる。

    Args:
        issues: レスポンスへ変換する未決事項一覧。
        total: 全件数。
        page: ページ番号。
        page_size: 1ページあたりの件数。

    Returns:
        未決事項一覧レスポンス。
    """
    return RequirementOpenIssueListResponse(
        items=[build_requirement_open_issue_response(issue) for issue in issues],
        total=total,
        page=page,
        page_size=page_size,
    )


def build_requirement_detail_response(
    detail: RequirementDetail,
) -> RequirementDetailRead:
    """要件詳細レスポンスを組み立てる。

    Args:
        detail: レスポンスへ変換する要件詳細。

    Returns:
        要件詳細読み取りレスポンス。
    """
    return RequirementDetailRead.model_validate(detail)


def build_requirement_detail_responses(
    details: list[RequirementDetail],
) -> list[RequirementDetailRead]:
    """要件詳細レスポンス一覧を組み立てる。

    Args:
        details: レスポンスへ変換する要件詳細一覧。

    Returns:
        要件詳細読み取りレスポンス一覧。
    """
    return [build_requirement_detail_response(detail) for detail in details]


def build_requirement_link_response(link: RequirementLink) -> RequirementLinkRead:
    """要件リンクレスポンスを組み立てる。

    Args:
        link: レスポンスへ変換する要件リンク。

    Returns:
        要件リンク読み取りレスポンス。
    """
    return RequirementLinkRead.model_validate(link)


def build_requirement_link_responses(
    links: list[RequirementLink],
) -> list[RequirementLinkRead]:
    """要件リンクレスポンス一覧を組み立てる。

    Args:
        links: レスポンスへ変換する要件リンク一覧。

    Returns:
        要件リンク読み取りレスポンス一覧。
    """
    return [build_requirement_link_response(link) for link in links]


def build_requirement_relation_response(
    relation: RequirementRelation,
) -> RequirementRelationRead:
    """要件関連レスポンスを組み立てる。

    Args:
        relation: レスポンスへ変換する要件関連。

    Returns:
        要件関連読み取りレスポンス。
    """
    return RequirementRelationRead.model_validate(relation)


def build_requirement_relation_responses(
    relations: list[RequirementRelation],
) -> list[RequirementRelationRead]:
    """要件関連レスポンス一覧を組み立てる。

    Args:
        relations: レスポンスへ変換する要件関連一覧。

    Returns:
        要件関連読み取りレスポンス一覧。
    """
    return [build_requirement_relation_response(relation) for relation in relations]


def build_requirement_review_response(
    review: RequirementReview,
) -> RequirementReviewRead:
    """要件レビューレスポンスを組み立てる。

    Args:
        review: レスポンスへ変換する要件レビュー。

    Returns:
        要件レビュー読み取りレスポンス。
    """
    return RequirementReviewRead.model_validate(review)


def build_requirement_review_responses(
    reviews: list[RequirementReview],
) -> list[RequirementReviewRead]:
    """要件レビューレスポンス一覧を組み立てる。

    Args:
        reviews: レスポンスへ変換する要件レビュー一覧。

    Returns:
        要件レビュー読み取りレスポンス一覧。
    """
    return [build_requirement_review_response(review) for review in reviews]


def build_requirement_revision_response(
    revision: RequirementRevision,
) -> RequirementRevisionRead:
    """要件改訂履歴レスポンスを組み立てる。

    Args:
        revision: レスポンスへ変換する要件改訂履歴。

    Returns:
        要件改訂履歴読み取りレスポンス。
    """
    return RequirementRevisionRead.model_validate(revision)


def build_requirement_revision_responses(
    revisions: list[RequirementRevision],
) -> list[RequirementRevisionRead]:
    """要件改訂履歴レスポンス一覧を組み立てる。

    Args:
        revisions: レスポンスへ変換する要件改訂履歴一覧。

    Returns:
        要件改訂履歴読み取りレスポンス一覧。
    """
    return [build_requirement_revision_response(revision) for revision in revisions]


def build_requirement_summary_response(
    *,
    requirement: Requirement,
    details: list[RequirementDetail],
    links: list[RequirementLink],
    comments: list[RequirementCommentRead],
    reviews: list[RequirementReview],
    revisions: list[RequirementRevision],
) -> RequirementSummaryRead:
    """要件詳細画面用の集約レスポンスを組み立てる。

    Args:
        requirement: 要件モデル。
        details: 要件詳細一覧。
        links: 要件リンク一覧。
        comments: 変換済みコメント一覧。
        reviews: 要件レビュー一覧。
        revisions: 要件改訂履歴一覧。

    Returns:
        要件詳細画面用の集約レスポンス。
    """
    return RequirementSummaryRead(
        requirement=build_requirement_response(requirement),
        details=build_requirement_detail_responses(details),
        links=build_requirement_link_responses(links),
        comments=comments,
        reviews=build_requirement_review_responses(reviews),
        revisions=build_requirement_revision_responses(revisions),
    )


def build_requirement_comment_response(
    comment: RequirementComment,
    user: User | None = None,
) -> RequirementCommentRead:
    """要件コメントレスポンスを組み立てる。

    Args:
        comment: レスポンスへ変換する要件コメント。
        user: コメント投稿者。

    Returns:
        要件コメント読み取りレスポンス。
    """
    return RequirementCommentRead.model_validate(comment).model_copy(
        update={
            "user": _build_change_log_user_response(user) if user is not None else None
        }
    )


def build_requirement_comment_responses(
    comments: list[RequirementComment],
    *,
    users_by_id: dict[int, User],
) -> list[RequirementCommentRead]:
    """要件コメントレスポンス一覧を組み立てる。

    Args:
        comments: レスポンスへ変換する要件コメント一覧。
        users_by_id: コメント投稿者IDをkeyにしたユーザー辞書。

    Returns:
        要件コメント読み取りレスポンス一覧。
    """
    return [
        build_requirement_comment_response(
            comment,
            user=users_by_id.get(comment.user_id),
        )
        for comment in comments
    ]


def build_requirement_target_comment_response(
    comment: RequirementTargetComment,
    *,
    user: User | None = None,
) -> RequirementTargetCommentRead:
    """要件定義対象コメントレスポンスを組み立てる。

    Args:
        comment: レスポンスへ変換する要件定義対象コメント。
        user: コメント投稿者。

    Returns:
        要件定義対象コメント読み取りレスポンス。
    """
    return RequirementTargetCommentRead.model_validate(comment).model_copy(
        update={
            "author": _build_change_log_user_response(user)
            if user is not None
            else None
        }
    )


def build_requirement_target_comment_responses(
    comments: list[RequirementTargetComment],
    *,
    users_by_id: dict[int, User],
) -> list[RequirementTargetCommentRead]:
    """要件定義対象コメントレスポンス一覧を組み立てる。

    Args:
        comments: レスポンスへ変換する要件定義対象コメント一覧。
        users_by_id: コメント投稿者IDをkeyにしたユーザー辞書。

    Returns:
        要件定義対象コメント読み取りレスポンス一覧。
    """
    return [
        build_requirement_target_comment_response(
            comment,
            user=users_by_id.get(comment.author_id),
        )
        for comment in comments
    ]


def build_requirement_change_log_response(
    change_log: RequirementChangeLog,
    *,
    users_by_id: dict[int, User],
) -> RequirementChangeLogRead:
    """要件定義変更履歴レスポンスを組み立てる。

    Args:
        change_log: レスポンスへ変換する要件定義変更履歴。
        users_by_id: 変更履歴に含まれるユーザー概要。

    Returns:
        要件定義変更履歴読み取りレスポンス。
    """
    response_users_by_id = {
        user_id: _build_change_log_user_response(user)
        for user_id, user in users_by_id.items()
    }
    field_name = cast(
        RequirementChangeLogFieldName | None,
        REQUIREMENT_CHANGE_LOG_FORMATTER.normalize_field_name(
            change_log.field_name,
        ),
    )
    return RequirementChangeLogRead(
        id=change_log.id,
        document_id=change_log.document_id,
        target_type=cast(
            RequirementChangeLogTargetTypeCode,
            REQUIREMENT_CHANGE_LOG_FORMATTER.normalize_target_type(
                change_log.target_type,
            ),
        ),
        target_id=change_log.target_id,
        action=cast(
            RequirementChangeLogActionCode,
            REQUIREMENT_CHANGE_LOG_FORMATTER.normalize_action(change_log.action),
        ),
        field_name=field_name,
        old_value=cast(
            ChangeLogValue,
            REQUIREMENT_CHANGE_LOG_FORMATTER.extract_change_value(
                change_log.old_value,
                field_name,
                users_by_id=response_users_by_id,
            ),
        ),
        new_value=cast(
            ChangeLogValue,
            REQUIREMENT_CHANGE_LOG_FORMATTER.extract_change_value(
                change_log.new_value,
                field_name,
                users_by_id=response_users_by_id,
            ),
        ),
        reason=change_log.reason,
        changed_by=change_log.changed_by,
        changed_by_user=(
            response_users_by_id.get(change_log.changed_by)
            if change_log.changed_by is not None
            else None
        ),
        changed_at=change_log.changed_at,
    )


def build_requirement_change_log_list_response(
    change_logs: list[RequirementChangeLog],
    *,
    users_by_id: dict[int, User],
    total: int,
    page: int,
    page_size: int,
) -> RequirementChangeLogListResponse:
    """要件定義変更履歴一覧レスポンスを組み立てる。

    Args:
        change_logs: レスポンスへ変換する要件定義変更履歴一覧。
        users_by_id: 変更履歴に含まれるユーザー概要。
        total: 全件数。
        page: ページ番号。
        page_size: 1ページあたりの件数。

    Returns:
        要件定義変更履歴一覧レスポンス。
    """
    return RequirementChangeLogListResponse(
        items=[
            build_requirement_change_log_response(
                change_log,
                users_by_id=users_by_id,
            )
            for change_log in change_logs
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


def _build_change_log_user_response(user: User) -> ChangeLogUserRead:
    """変更履歴の変更者として返す軽量ユーザー情報を組み立てる。"""
    return ChangeLogUserRead(
        id=user.id,
        name=user.name,
        email=user.email,
        avatar_url=None,
    )
