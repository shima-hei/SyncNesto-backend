"""レスポンス表示用ユーザー情報の組み立て処理を提供するモジュール。"""

from sqlalchemy.orm import Session

from app.repositories.user import UserRepository
from app.schemas.change_log import ChangeLogUserRead


def build_response_users_by_id(
    db: Session,
    user_repository: UserRepository,
    user_ids: list[int | None],
) -> dict[int, ChangeLogUserRead]:
    """ユーザーID一覧からレスポンス表示用ユーザー情報を取得する。"""
    ids = sorted({user_id for user_id in user_ids if user_id is not None})
    users = user_repository.list_by_ids(db, ids)
    return {
        user.id: ChangeLogUserRead(
            id=user.id,
            name=user.name,
            email=user.email,
            avatar_url=None,
        )
        for user in users
    }
