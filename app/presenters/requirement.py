"""要件定義関連レスポンスのPresenterを定義するモジュール。"""

from app.models.requirement import RequirementDocument
from app.models.user import User
from app.schemas.requirement import RequirementDocumentRead
from app.schemas.user import UserSummary
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
