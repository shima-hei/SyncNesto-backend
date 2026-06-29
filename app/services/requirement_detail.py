"""要件詳細Serviceを定義するモジュール。"""

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import NotFoundError
from app.models.requirement import (
    RequirementDetail,
)
from app.schemas.requirement import (
    RequirementDetailCreate,
    RequirementDetailUpdate,
)
from app.services.requirement_change_log import (
    RequirementChangeLogAction,
    RequirementChangeLogTargetType,
)
from app.services.requirement_child_base import RequirementChildBaseService


class RequirementDetailService(RequirementChildBaseService):
    """要件詳細Serviceを定義するモジュール。"""

    def create_detail(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        detail_in: RequirementDetailCreate,
        actor_id: int | None = None,
    ) -> RequirementDetail:
        """要件詳細を作成する。

        Args:
            db: DBセッション。
            project_id: 作成対象のプロジェクトID。
            requirement_id: 作成対象の要件ID。
            detail_in: 要件詳細の作成入力値。
            actor_id: 操作ユーザーID。

        Returns:
            作成された要件詳細。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        detail = self.detail_repository.create(
            db,
            requirement_id=requirement_id,
            detail_in=detail_in,
        )
        self._record_child_change_log(
            db,
            requirement=requirement,
            target_type=RequirementChangeLogTargetType.DETAIL,
            target_id=detail.id,
            action=RequirementChangeLogAction.CREATED,
            new_value=self._build_detail_snapshot(detail),
            changed_by=actor_id,
        )
        return detail
    def list_details(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
    ) -> list[RequirementDetail]:
        """要件詳細一覧を取得する。

        Args:
            db: DBセッション。
            project_id: 取得対象のプロジェクトID。
            requirement_id: 取得対象の要件ID。

        Returns:
            要件詳細一覧。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        return self.detail_repository.list_by_requirement(db, requirement_id)
    def update_detail(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        detail_id: int,
        detail_in: RequirementDetailUpdate,
        actor_id: int | None = None,
    ) -> RequirementDetail:
        """要件詳細を更新する。

        Args:
            db: DBセッション。
            project_id: 更新対象のプロジェクトID。
            requirement_id: 更新対象の要件ID。
            detail_id: 更新対象の要件詳細ID。
            detail_in: 要件詳細の更新入力値。
            actor_id: 操作ユーザーID。

        Returns:
            更新された要件詳細。

        Raises:
            NotFoundError: 要件詳細が存在しない、または要件に属さない場合。
        """
        detail = self._get_detail_in_requirement(
            db,
            project_id,
            requirement_id,
            detail_id,
        )
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        before_value = self._build_detail_snapshot(detail)
        updated_detail = self.detail_repository.update(
            db,
            detail=detail,
            detail_in=detail_in,
        )
        self._record_child_change_log(
            db,
            requirement=requirement,
            target_type=RequirementChangeLogTargetType.DETAIL,
            target_id=updated_detail.id,
            action=RequirementChangeLogAction.UPDATED,
            old_value=before_value,
            new_value=self._build_detail_snapshot(updated_detail),
            changed_by=actor_id,
        )
        return updated_detail
    def delete_detail(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        detail_id: int,
        actor_id: int | None = None,
    ) -> None:
        """要件詳細を物理削除する。

        Args:
            db: DBセッション。
            project_id: 削除対象のプロジェクトID。
            requirement_id: 削除対象の要件ID。
            detail_id: 削除対象の要件詳細ID。
            actor_id: 操作ユーザーID。

        Raises:
            NotFoundError: 要件詳細が存在しない、または要件に属さない場合。
        """
        detail = self._get_detail_in_requirement(
            db,
            project_id,
            requirement_id,
            detail_id,
        )
        requirement = self._ensure_requirement_in_project(
            db,
            project_id,
            requirement_id,
        )
        before_value = self._build_detail_snapshot(detail)
        self.detail_repository.delete(db, detail)
        self._record_child_change_log(
            db,
            requirement=requirement,
            target_type=RequirementChangeLogTargetType.DETAIL,
            target_id=detail_id,
            action=RequirementChangeLogAction.DELETED,
            old_value=before_value,
            changed_by=actor_id,
        )
    def _get_detail_in_requirement(
        self,
        db: Session,
        project_id: int,
        requirement_id: int,
        detail_id: int,
    ) -> RequirementDetail:
        """要件配下の詳細を取得する。

        Args:
            db: DBセッション。
            project_id: 所属確認対象のプロジェクトID。
            requirement_id: 所属確認対象の要件ID。
            detail_id: 取得対象の要件詳細ID。

        Returns:
            取得した要件詳細。

        Raises:
            NotFoundError: 要件詳細が存在しない、または要件に属さない場合。
        """
        self._ensure_requirement_in_project(db, project_id, requirement_id)
        detail = self.detail_repository.get_by_id(db, detail_id)
        if detail is None or detail.requirement_id != requirement_id:
            raise NotFoundError(error_messages.REQUIREMENT_DETAIL_NOT_FOUND)
        return detail
