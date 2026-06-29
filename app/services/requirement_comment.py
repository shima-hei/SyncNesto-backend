"""要件コメントServiceを定義するモジュール。"""

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import NotFoundError
from app.models.requirement import (
    RequirementComment,
)
from app.schemas.requirement import (
    RequirementCommentCreate,
)
from app.services.requirement_child_base import RequirementChildBaseService


class RequirementCommentService(RequirementChildBaseService):
    """要件コメントServiceを定義するモジュール。"""

    def create_comment(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        user_id: int,
        comment_in: RequirementCommentCreate,
    ) -> RequirementComment:
        """要件コメントを作成する。

        Args:
            db: DBセッション。
            project_id: 作成対象のプロジェクトID。
            requirement_id: 作成対象の要件ID。
            user_id: コメント作成ユーザーID。
            comment_in: 要件コメントの作成入力値。

        Returns:
            作成された要件コメント。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        return self.comment_repository.create(
            db,
            requirement_id=requirement_id,
            user_id=user_id,
            comment_in=comment_in,
        )
    def list_comments(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
    ) -> list[RequirementComment]:
        """要件コメント一覧を取得する。

        Args:
            db: DBセッション。
            project_id: 取得対象のプロジェクトID。
            requirement_id: 取得対象の要件ID。

        Returns:
            要件コメント一覧。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        return self.comment_repository.list_by_requirement(db, requirement_id)
    def delete_comment(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        comment_id: int,
    ) -> None:
        """要件コメントを物理削除する。

        Args:
            db: DBセッション。
            project_id: 削除対象のプロジェクトID。
            requirement_id: 削除対象の要件ID。
            comment_id: 削除対象の要件コメントID。

        Raises:
            NotFoundError: 要件コメントが存在しない、または要件に属さない場合。
        """
        comment = self._get_comment_in_requirement(
            db,
            project_id,
            requirement_id,
            comment_id,
        )
        self.comment_repository.delete(db, comment)
    def _get_comment_in_requirement(
        self,
        db: Session,
        project_id: int,
        requirement_id: int,
        comment_id: int,
    ) -> RequirementComment:
        """要件配下のコメントを取得する。

        Args:
            db: DBセッション。
            project_id: 所属確認対象のプロジェクトID。
            requirement_id: 所属確認対象の要件ID。
            comment_id: 取得対象の要件コメントID。

        Returns:
            取得した要件コメント。

        Raises:
            NotFoundError: 要件コメントが存在しない、または要件に属さない場合。
        """
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        comment = self.comment_repository.get_by_id(db, comment_id)
        if comment is None or comment.requirement_id != requirement_id:
            raise NotFoundError(error_messages.REQUIREMENT_COMMENT_NOT_FOUND)
        return comment
