"""要件定義セクションサービスを定義するモジュール。"""

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import NotFoundError
from app.models.requirement import RequirementDocument, RequirementSection
from app.repositories.requirement import (
    RequirementDocumentRepository,
    RequirementSectionRepository,
)
from app.schemas.requirement import (
    RequirementSectionCreate,
    RequirementSectionRead,
    RequirementSectionSortUpdate,
    RequirementSectionUpdate,
)
from app.services.change_log_formatter import (
    build_changed_field_snapshots,
    build_update_change_log_entry,
)
from app.services.conflict import raise_if_version_conflict
from app.services.requirement_change_log import (
    RequirementChangeLogAction,
    RequirementChangeLogService,
    RequirementChangeLogTargetType,
)

REQUIREMENT_SECTION_UPDATABLE_FIELDS = {
    "title",
    "section_type",
    "content",
    "sort_order",
    "status",
}


class RequirementSectionService:
    """要件定義セクションに関するビジネスロジックを提供する。"""

    def __init__(
        self,
        repository: RequirementSectionRepository | None = None,
        document_repository: RequirementDocumentRepository | None = None,
        change_log_service: RequirementChangeLogService | None = None,
    ) -> None:
        """RequirementSectionServiceを初期化する。

        Args:
            repository: 要件定義セクションRepository。
            document_repository: 要件定義書Repository。
            change_log_service: 要件定義変更履歴Service。
        """
        self.repository = repository or RequirementSectionRepository()
        self.document_repository = (
            document_repository or RequirementDocumentRepository()
        )
        self.change_log_service = change_log_service or RequirementChangeLogService()

    def create_section(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
        section_in: RequirementSectionCreate,
        actor_id: int | None = None,
    ) -> RequirementSection:
        """要件定義セクションを作成する。

        Args:
            db: DBセッション。
            project_id: 作成対象のプロジェクトID。
            document_id: 作成対象の要件定義書ID。
            section_in: セクション作成入力値。
            actor_id: 操作ユーザーID。

        Returns:
            作成された要件定義セクション。

        Raises:
            NotFoundError: 要件定義書が存在しない、またはプロジェクトに属さない場合。
        """
        self._get_document_in_project(
            db,
            project_id=project_id,
            document_id=document_id,
        )
        section = self.repository.create(
            db,
            document_id=document_id,
            section_in=section_in,
            actor_id=actor_id,
        )
        self._record_section_change_log(
            db,
            section=section,
            action=RequirementChangeLogAction.CREATED,
            new_value=self._build_section_snapshot(section),
            changed_by=actor_id,
        )
        return section

    def list_sections(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
    ) -> list[RequirementSection]:
        """要件定義書内のセクション一覧を取得する。

        Args:
            db: DBセッション。
            project_id: 取得対象のプロジェクトID。
            document_id: 取得対象の要件定義書ID。

        Returns:
            要件定義セクション一覧。

        Raises:
            NotFoundError: 要件定義書が存在しない、またはプロジェクトに属さない場合。
        """
        self._get_document_in_project(
            db,
            project_id=project_id,
            document_id=document_id,
        )
        return self.repository.list_by_document(db, document_id)

    def get_section(
        self,
        db: Session,
        *,
        project_id: int,
        section_id: int,
    ) -> RequirementSection:
        """要件定義セクションを取得する。

        Args:
            db: DBセッション。
            project_id: 取得対象のプロジェクトID。
            section_id: 取得対象の要件定義セクションID。

        Returns:
            取得した要件定義セクション。

        Raises:
            NotFoundError: セクションが存在しない、またはプロジェクトに属さない場合。
        """
        section = self.repository.get_by_id(db, section_id)
        if section is None:
            raise NotFoundError(error_messages.REQUIREMENT_SECTION_NOT_FOUND)

        self._get_document_in_project(
            db,
            project_id=project_id,
            document_id=section.document_id,
        )
        return section

    def update_section(
        self,
        db: Session,
        *,
        project_id: int,
        section_id: int,
        section_in: RequirementSectionUpdate,
        actor_id: int | None = None,
    ) -> RequirementSection:
        """要件定義セクションを更新する。

        Args:
            db: DBセッション。
            project_id: 更新対象のプロジェクトID。
            section_id: 更新対象の要件定義セクションID。
            section_in: セクション更新入力値。
            actor_id: 操作ユーザーID。

        Returns:
            更新された要件定義セクション。

        Raises:
            NotFoundError: セクションが存在しない、またはプロジェクトに属さない場合。
        """
        section = self.get_section(db, project_id=project_id, section_id=section_id)
        raise_if_version_conflict(
            current_version=section.version,
            requested_version=section_in.version,
            current=RequirementSectionRead.model_validate(section).model_dump(),
        )
        before_value = self._build_section_snapshot(section)
        updated_section = self.repository.update(
            db,
            section=section,
            section_in=section_in,
            actor_id=actor_id,
        )
        self._record_section_update_change_log(
            db,
            section=updated_section,
            old_snapshot=before_value,
            new_snapshot=self._build_section_snapshot(updated_section),
            updated_fields=(
                section_in.model_fields_set - {"version", "reason"}
            )
            & REQUIREMENT_SECTION_UPDATABLE_FIELDS,
            changed_by=actor_id,
        )
        return updated_section

    def update_sort_order(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
        sort_in: RequirementSectionSortUpdate,
        actor_id: int | None = None,
    ) -> list[RequirementSection]:
        """要件定義セクションの表示順を更新する。

        Args:
            db: DBセッション。
            project_id: 更新対象のプロジェクトID。
            document_id: 更新対象の要件定義書ID。
            sort_in: 表示順更新入力値。
            actor_id: 操作ユーザーID。

        Returns:
            更新後の要件定義セクション一覧。

        Raises:
            NotFoundError: 要件定義書またはセクションが存在しない場合。
        """
        self._get_document_in_project(
            db,
            project_id=project_id,
            document_id=document_id,
        )
        sections_by_id = {
            section.id: section
            for section in self.repository.list_by_document(db, document_id)
        }
        sort_orders_by_id: dict[int, int] = {}
        target_sections: list[RequirementSection] = []
        for item in sort_in.items:
            section = sections_by_id.get(item.section_id)
            if section is None:
                raise NotFoundError(error_messages.REQUIREMENT_SECTION_NOT_FOUND)
            raise_if_version_conflict(
                current_version=section.version,
                requested_version=item.version,
                current=RequirementSectionRead.model_validate(section).model_dump(),
            )
            sort_orders_by_id[section.id] = item.sort_order
            target_sections.append(section)

        if not target_sections:
            return self.repository.list_by_document(db, document_id)
        updated_sections = self.repository.update_sort_orders(
            db,
            sections=target_sections,
            sort_orders_by_id=sort_orders_by_id,
            actor_id=actor_id,
        )
        self.change_log_service.record(
            db,
            document_id=document_id,
            target_type=RequirementChangeLogTargetType.DOCUMENT,
            target_id=document_id,
            action=RequirementChangeLogAction.SORTED,
            new_value={
                "sort_orders": [
                    {"section_id": section.id, "sort_order": section.sort_order}
                    for section in updated_sections
                ]
            },
            changed_by=actor_id,
        )
        return updated_sections

    def delete_section(
        self,
        db: Session,
        *,
        project_id: int,
        section_id: int,
        actor_id: int | None = None,
    ) -> None:
        """要件定義セクションを論理削除する。

        Args:
            db: DBセッション。
            project_id: 削除対象のプロジェクトID。
            section_id: 削除対象の要件定義セクションID。
            actor_id: 操作ユーザーID。

        Raises:
            NotFoundError: セクションが存在しない、またはプロジェクトに属さない場合。
        """
        section = self.get_section(db, project_id=project_id, section_id=section_id)
        before_value = self._build_section_snapshot(section)
        self.repository.soft_delete(db, section=section, actor_id=actor_id)
        self._record_section_change_log(
            db,
            section=section,
            action=RequirementChangeLogAction.DELETED,
            old_value=before_value,
            changed_by=actor_id,
        )

    def _get_document_in_project(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
    ) -> RequirementDocument:
        """プロジェクト内の要件定義書を取得する。"""
        document = self.document_repository.get_by_id(db, document_id)
        if document is None or document.project_id != project_id:
            raise NotFoundError(error_messages.REQUIREMENT_DOCUMENT_NOT_FOUND)
        return document

    def _record_section_change_log(
        self,
        db: Session,
        *,
        section: RequirementSection,
        action: str,
        old_value: dict[str, object] | None = None,
        new_value: dict[str, object] | None = None,
        changed_by: int | None = None,
    ) -> None:
        """要件定義セクションの変更履歴を記録する。"""
        self.change_log_service.record(
            db,
            document_id=section.document_id,
            target_type=RequirementChangeLogTargetType.SECTION,
            target_id=section.id,
            action=action,
            old_value=old_value,
            new_value=new_value,
            changed_by=changed_by,
        )

    def _record_section_update_change_log(
        self,
        db: Session,
        *,
        section: RequirementSection,
        old_snapshot: dict[str, object],
        new_snapshot: dict[str, object],
        updated_fields: set[str],
        changed_by: int | None = None,
    ) -> None:
        """要件定義セクション更新の変更履歴を記録する。"""
        changed_fields, old_value, new_value = build_changed_field_snapshots(
            old_snapshot,
            new_snapshot,
            updated_fields,
        )
        change_log_entry = build_update_change_log_entry(
            updated_fields=changed_fields,
            old_values=old_value,
            new_values=new_value,
            default_action=RequirementChangeLogAction.UPDATED,
        )
        if change_log_entry is None:
            return

        self._record_section_change_log(
            db,
            section=section,
            action=change_log_entry.action,
            old_value=change_log_entry.old_value,
            new_value=change_log_entry.new_value,
            changed_by=changed_by,
        )

    def _build_section_snapshot(
        self,
        section: RequirementSection,
    ) -> dict[str, object]:
        """変更履歴に保存するセクションスナップショットを作成する。"""
        return {
            "id": section.id,
            "document_id": section.document_id,
            "title": section.title,
            "section_type": section.section_type,
            "content": section.content,
            "sort_order": section.sort_order,
            "status": section.status,
            "version": section.version,
        }
