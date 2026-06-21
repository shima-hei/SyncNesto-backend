"""要件サービスを定義するモジュール。"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import DuplicateResourceError, NotFoundError
from app.models.requirement import (
    Requirement,
    RequirementDocument,
    RequirementRevision,
    RequirementSection,
)
from app.repositories.requirement import (
    RequirementDocumentRepository,
    RequirementRepository,
    RequirementRevisionRepository,
    RequirementSectionRepository,
)
from app.schemas.requirement import (
    RequirementCreate,
    RequirementRead,
    RequirementUpdate,
)
from app.services.conflict import (
    raise_duplicate_after_rollback,
    raise_if_version_conflict,
)


class RequirementService:
    """要件に関するビジネスロジックを提供する。"""

    def __init__(
        self,
        repository: RequirementRepository | None = None,
        document_repository: RequirementDocumentRepository | None = None,
        section_repository: RequirementSectionRepository | None = None,
        revision_repository: RequirementRevisionRepository | None = None,
    ) -> None:
        """RequirementServiceを初期化する。

        Args:
            repository: 要件Repository。
            document_repository: 要件定義書Repository。
            section_repository: 要件定義セクションRepository。
            revision_repository: 要件改訂履歴Repository。
        """
        self.repository = repository or RequirementRepository()
        self.document_repository = (
            document_repository or RequirementDocumentRepository()
        )
        self.section_repository = section_repository or RequirementSectionRepository()
        self.revision_repository = (
            revision_repository or RequirementRevisionRepository()
        )

    def create_requirement(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_in: RequirementCreate,
        actor_id: int | None = None,
    ) -> Requirement:
        """要件を作成する。

        Args:
            db: DBセッション。
            project_id: 要件を作成するプロジェクトID。
            requirement_in: 要件の作成入力値。
            actor_id: 操作ユーザーID。

        Returns:
            作成された要件。

        Raises:
            NotFoundError: 要件定義書が存在しない、またはプロジェクトに属さない場合。
            DuplicateResourceError: requirement_codeが重複している場合。
        """
        self._get_document_in_project(
            db,
            project_id=project_id,
            document_id=requirement_in.document_id,
        )
        if requirement_in.section_id is not None:
            self._get_section_in_document(
                db,
                document_id=requirement_in.document_id,
                section_id=requirement_in.section_id,
            )
        if (
            self.repository.get_by_document_requirement_code(
                db,
                document_id=requirement_in.document_id,
                requirement_code=requirement_in.requirement_code,
            )
            is not None
        ):
            raise DuplicateResourceError(
                error_messages.REQUIREMENT_CODE_ALREADY_EXISTS
            )

        try:
            return self.repository.create(
                db,
                requirement_in=requirement_in,
                actor_id=actor_id,
            )
        except IntegrityError as exc:
            raise_duplicate_after_rollback(
                db,
                error_messages.REQUIREMENT_CODE_ALREADY_EXISTS,
                exc,
            )

    def list_requirements_paginated(
        self,
        db: Session,
        *,
        project_id: int,
        page: int,
        page_size: int,
        document_id: int | None = None,
        q: str | None = None,
        status: str | None = None,
        requirement_type: str | None = None,
        section_id: int | None = None,
    ) -> tuple[list[Requirement], int]:
        """プロジェクト内の要件一覧をページング付きで取得する。

        Args:
            db: DBセッション。
            project_id: 一覧取得対象のプロジェクトID。
            page: 取得ページ番号。
            page_size: 1ページあたりの取得件数。
            document_id: 絞り込み対象の要件定義書ID。
            q: 検索キーワード。
            status: 絞り込み対象のステータス。
            requirement_type: 絞り込み対象の要件種別。
            section_id: 絞り込み対象の要件定義セクションID。

        Returns:
            要件一覧と総件数。

        Raises:
            NotFoundError: 指定された要件定義書がプロジェクトに属さない場合。
        """
        if document_id is not None:
            document = self._get_document_in_project(
                db,
                project_id=project_id,
                document_id=document_id,
            )
            document_ids = [document_id]
        else:
            document_ids = self.document_repository.list_ids_by_project(db, project_id)

        if not document_ids:
            return [], 0
        if section_id is not None:
            section = self._get_section_in_project(
                db,
                project_id=project_id,
                section_id=section_id,
            )
            if document_id is not None and section.document_id != document.id:
                raise NotFoundError(error_messages.REQUIREMENT_SECTION_NOT_FOUND)

        return self.repository.list_paginated(
            db,
            document_ids=document_ids,
            page=page,
            page_size=page_size,
            q=q,
            status=status,
            requirement_type=requirement_type,
            section_id=section_id,
        )

    def get_requirement(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
    ) -> Requirement:
        """要件を取得する。

        Args:
            db: DBセッション。
            project_id: 取得対象のプロジェクトID。
            requirement_id: 取得対象の要件ID。

        Returns:
            取得した要件。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
        requirement = self.repository.get_by_id(db, requirement_id)
        if requirement is None:
            raise NotFoundError(error_messages.REQUIREMENT_NOT_FOUND)

        self._get_document_in_project(
            db,
            project_id=project_id,
            document_id=requirement.document_id,
        )
        return requirement

    def update_requirement(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        requirement_in: RequirementUpdate,
        actor_id: int | None = None,
    ) -> Requirement:
        """要件を更新し、改訂履歴を作成する。

        Args:
            db: DBセッション。
            project_id: 更新対象のプロジェクトID。
            requirement_id: 更新対象の要件ID。
            requirement_in: 要件の更新入力値。
            actor_id: 操作ユーザーID。

        Returns:
            更新された要件。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
            VersionConflictError: versionが一致しない場合。
            DuplicateResourceError: requirement_codeが重複している場合。
        """
        requirement = self.get_requirement(
            db,
            project_id=project_id,
            requirement_id=requirement_id,
        )
        raise_if_version_conflict(
            current_version=requirement.version,
            requested_version=requirement_in.version,
            current=RequirementRead.model_validate(requirement).model_dump(),
        )

        if (
            requirement_in.requirement_code is not None
            and requirement_in.requirement_code != requirement.requirement_code
            and self.repository.get_by_document_requirement_code(
                db,
                document_id=requirement.document_id,
                requirement_code=requirement_in.requirement_code,
            )
            is not None
        ):
            raise DuplicateResourceError(
                error_messages.REQUIREMENT_CODE_ALREADY_EXISTS
            )
        if (
            "section_id" in requirement_in.model_fields_set
            and requirement_in.section_id is not None
        ):
            self._get_section_in_document(
                db,
                document_id=requirement.document_id,
                section_id=requirement_in.section_id,
            )

        before_value = self._build_revision_snapshot(requirement)
        try:
            updated_requirement = self.repository.update(
                db,
                requirement=requirement,
                requirement_in=requirement_in,
                actor_id=actor_id,
            )
            after_value = self._build_revision_snapshot(updated_requirement)
            self.revision_repository.create(
                db,
                requirement_id=updated_requirement.id,
                version=updated_requirement.version,
                changed_by=actor_id,
                change_summary=requirement_in.change_summary,
                before_value=before_value,
                after_value=after_value,
                reason=requirement_in.reason,
            )
            db.commit()
            db.refresh(updated_requirement)
            return updated_requirement
        except IntegrityError as exc:
            raise_duplicate_after_rollback(
                db,
                error_messages.REQUIREMENT_CODE_ALREADY_EXISTS,
                exc,
            )

    def delete_requirement(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
        actor_id: int | None = None,
    ) -> None:
        """要件を論理削除する。

        Args:
            db: DBセッション。
            project_id: 削除対象のプロジェクトID。
            requirement_id: 削除対象の要件ID。
            actor_id: 操作ユーザーID。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
        requirement = self.get_requirement(
            db,
            project_id=project_id,
            requirement_id=requirement_id,
        )
        self.repository.soft_delete(db, requirement=requirement, actor_id=actor_id)

    def list_revisions(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
    ) -> list[RequirementRevision]:
        """要件の改訂履歴一覧を取得する。

        Args:
            db: DBセッション。
            project_id: 取得対象のプロジェクトID。
            requirement_id: 改訂履歴を取得する要件ID。

        Returns:
            要件の改訂履歴一覧。

        Raises:
            NotFoundError: 要件が存在しない、またはプロジェクトに属さない場合。
        """
        requirement = self.get_requirement(
            db,
            project_id=project_id,
            requirement_id=requirement_id,
        )
        return self.revision_repository.list_by_requirement(db, requirement.id)

    def _get_document_in_project(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
    ) -> RequirementDocument:
        """プロジェクト内の要件定義書を取得する。

        Args:
            db: DBセッション。
            project_id: 所属確認対象のプロジェクトID。
            document_id: 取得対象の要件定義書ID。

        Returns:
            取得した要件定義書。

        Raises:
            NotFoundError: 要件定義書が存在しない、またはプロジェクトに属さない場合。
        """
        document = self.document_repository.get_by_id(db, document_id)
        if document is None or document.project_id != project_id:
            raise NotFoundError(error_messages.REQUIREMENT_DOCUMENT_NOT_FOUND)
        return document

    def _get_section_in_document(
        self,
        db: Session,
        *,
        document_id: int,
        section_id: int,
    ) -> RequirementSection:
        """要件定義書内のセクションを取得する。

        Args:
            db: DBセッション。
            document_id: 所属確認対象の要件定義書ID。
            section_id: 取得対象の要件定義セクションID。

        Returns:
            取得した要件定義セクション。

        Raises:
            NotFoundError: セクションが存在しない、または要件定義書に属さない場合。
        """
        section = self.section_repository.get_by_id(db, section_id)
        if section is None or section.document_id != document_id:
            raise NotFoundError(error_messages.REQUIREMENT_SECTION_NOT_FOUND)
        return section

    def _get_section_in_project(
        self,
        db: Session,
        *,
        project_id: int,
        section_id: int,
    ) -> RequirementSection:
        """プロジェクト内のセクションを取得する。

        Args:
            db: DBセッション。
            project_id: 所属確認対象のプロジェクトID。
            section_id: 取得対象の要件定義セクションID。

        Returns:
            取得した要件定義セクション。

        Raises:
            NotFoundError: セクションが存在しない、またはプロジェクトに属さない場合。
        """
        section = self.section_repository.get_by_id(db, section_id)
        if section is None:
            raise NotFoundError(error_messages.REQUIREMENT_SECTION_NOT_FOUND)
        self._get_document_in_project(
            db,
            project_id=project_id,
            document_id=section.document_id,
        )
        return section

    def _build_revision_snapshot(self, requirement: Requirement) -> dict[str, object]:
        """改訂履歴に保存する要件スナップショットを作成する。

        Args:
            requirement: スナップショット化する要件。

        Returns:
            改訂履歴に保存する要件情報。
        """
        return {
            "id": requirement.id,
            "document_id": requirement.document_id,
            "section_id": requirement.section_id,
            "requirement_code": requirement.requirement_code,
            "requirement_type": requirement.requirement_type,
            "category": requirement.category,
            "title": requirement.title,
            "description": requirement.description,
            "rationale": requirement.rationale,
            "acceptance_criteria": requirement.acceptance_criteria,
            "priority": requirement.priority,
            "status": requirement.status,
            "source": requirement.source,
            "owner_id": requirement.owner_id,
            "approved_by": requirement.approved_by,
            "version": requirement.version,
        }
