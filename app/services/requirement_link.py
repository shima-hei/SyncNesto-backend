"""要件リンクServiceを定義するモジュール。"""

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import NotFoundError
from app.models.requirement import (
    RequirementLink,
)
from app.schemas.requirement import (
    RequirementLinkCreate,
    RequirementLinkUpdate,
)
from app.services.requirement_change_log import (
    RequirementChangeLogAction,
    RequirementChangeLogTargetType,
)
from app.services.requirement_child_base import RequirementChildBaseService


class RequirementLinkService(RequirementChildBaseService):
    """要件リンクServiceを定義するモジュール。"""

    def create_link(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        link_in: RequirementLinkCreate,
        actor_id: int | None = None,
    ) -> RequirementLink:
        """要件リンクを作成する。

        Args:
            db: DBセッション。
            project_id: 作成対象のプロジェクトID。
            requirement_id: 作成対象の要件ID。
            link_in: 要件リンクの作成入力値。
            actor_id: 操作ユーザーID。

        Returns:
            作成された要件リンク。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        link = self.link_repository.create(
            db,
            requirement_id=requirement_id,
            link_in=link_in,
        )
        self._record_child_change_log(
            db,
            requirement=requirement,
            target_type=RequirementChangeLogTargetType.LINK,
            target_id=link.id,
            action=RequirementChangeLogAction.CREATED,
            new_value=self._build_link_snapshot(link),
            changed_by=actor_id,
        )
        return link

    def update_link(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        link_id: int,
        link_in: RequirementLinkUpdate,
        actor_id: int | None = None,
    ) -> RequirementLink:
        """要件リンクを更新する。"""
        link = self._get_link_in_requirement(db, project_id, requirement_id, link_id)
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        before_value = self._build_link_snapshot(link)
        updated_link = self.link_repository.update(db, link, link_in)
        self._record_child_change_log(
            db,
            requirement=requirement,
            target_type=RequirementChangeLogTargetType.LINK,
            target_id=updated_link.id,
            action=RequirementChangeLogAction.UPDATED,
            old_value=before_value,
            new_value=self._build_link_snapshot(updated_link),
            changed_by=actor_id,
        )
        return updated_link

    def list_links(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
    ) -> list[RequirementLink]:
        """要件リンク一覧を取得する。

        Args:
            db: DBセッション。
            project_id: 取得対象のプロジェクトID。
            requirement_id: 取得対象の要件ID。

        Returns:
            要件リンク一覧。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        return self.link_repository.list_by_requirement(db, requirement_id)

    def delete_link(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        link_id: int,
        actor_id: int | None = None,
    ) -> None:
        """要件リンクを物理削除する。

        Args:
            db: DBセッション。
            project_id: 削除対象のプロジェクトID。
            requirement_id: 削除対象の要件ID。
            link_id: 削除対象の要件リンクID。
            actor_id: 操作ユーザーID。

        Raises:
            NotFoundError: 要件リンクが存在しない、または要件に属さない場合。
        """
        link = self._get_link_in_requirement(db, project_id, requirement_id, link_id)
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        before_value = self._build_link_snapshot(link)
        self.link_repository.delete(db, link)
        self._record_child_change_log(
            db,
            requirement=requirement,
            target_type=RequirementChangeLogTargetType.LINK,
            target_id=link_id,
            action=RequirementChangeLogAction.DELETED,
            old_value=before_value,
            changed_by=actor_id,
        )

    def _get_link_in_requirement(
        self,
        db: Session,
        project_id: int,
        requirement_id: int,
        link_id: int,
    ) -> RequirementLink:
        """要件配下のリンクを取得する。

        Args:
            db: DBセッション。
            project_id: 所属確認対象のプロジェクトID。
            requirement_id: 所属確認対象の要件ID。
            link_id: 取得対象の要件リンクID。

        Returns:
            取得した要件リンク。

        Raises:
            NotFoundError: 要件リンクが存在しない、または要件に属さない場合。
        """
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        link = self.link_repository.get_by_id(db, link_id)
        if link is None or link.requirement_id != requirement_id:
            raise NotFoundError(error_messages.REQUIREMENT_LINK_NOT_FOUND)
        return link
