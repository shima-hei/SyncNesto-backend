"""要件定義書サービスを定義するモジュール。"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import DuplicateResourceError, NotFoundError
from app.models.requirement import RequirementDocument
from app.repositories.project import ProjectRepository
from app.repositories.requirement import RequirementDocumentRepository
from app.schemas.requirement import (
    RequirementDocumentCreate,
    RequirementDocumentRead,
    RequirementDocumentUpdate,
)
from app.services.conflict import (
    raise_duplicate_after_rollback,
    raise_if_version_conflict,
)


class RequirementDocumentService:
    """要件定義書に関するビジネスロジックを提供する。"""

    def __init__(
        self,
        repository: RequirementDocumentRepository | None = None,
        project_repository: ProjectRepository | None = None,
    ) -> None:
        """RequirementDocumentServiceを初期化する。

        Args:
            repository: 要件定義書Repository。
            project_repository: プロジェクトRepository。
        """
        self.repository = repository or RequirementDocumentRepository()
        self.project_repository = project_repository or ProjectRepository()

    def create_document(
        self,
        db: Session,
        *,
        project_id: int,
        document_in: RequirementDocumentCreate,
        actor_id: int | None = None,
    ) -> RequirementDocument:
        """要件定義書を作成する。

        Args:
            db: DBセッション。
            project_id: 要件定義書を作成するプロジェクトID。
            document_in: 要件定義書の作成入力値。
            actor_id: 操作ユーザーID。

        Returns:
            作成された要件定義書。

        Raises:
            NotFoundError: プロジェクトが存在しない場合。
            DuplicateResourceError: document_codeが重複している場合。
        """
        self._ensure_project_exists(db, project_id)
        if (
            self.repository.get_by_project_document_code(
                db,
                project_id=project_id,
                document_code=document_in.document_code,
            )
            is not None
        ):
            raise DuplicateResourceError(
                error_messages.REQUIREMENT_DOCUMENT_CODE_ALREADY_EXISTS
            )

        try:
            return self.repository.create(
                db,
                project_id=project_id,
                document_in=document_in,
                actor_id=actor_id,
            )
        except IntegrityError as exc:
            raise_duplicate_after_rollback(
                db,
                error_messages.REQUIREMENT_DOCUMENT_CODE_ALREADY_EXISTS,
                exc,
            )

    def list_documents_paginated(
        self,
        db: Session,
        *,
        project_id: int,
        page: int,
        page_size: int,
        q: str | None = None,
        status: str | None = None,
    ) -> tuple[list[RequirementDocument], int]:
        """プロジェクト内の要件定義書一覧をページング付きで取得する。

        Args:
            db: DBセッション。
            project_id: 一覧取得対象のプロジェクトID。
            page: 取得ページ番号。
            page_size: 1ページあたりの取得件数。
            q: 検索キーワード。
            status: 絞り込み対象のステータス。

        Returns:
            要件定義書一覧と総件数。

        Raises:
            NotFoundError: プロジェクトが存在しない場合。
        """
        self._ensure_project_exists(db, project_id)
        return self.repository.list_paginated(
            db,
            project_id=project_id,
            page=page,
            page_size=page_size,
            q=q,
            status=status,
        )

    def get_document(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
    ) -> RequirementDocument:
        """要件定義書を取得する。

        Args:
            db: DBセッション。
            project_id: 取得対象のプロジェクトID。
            document_id: 取得対象の要件定義書ID。

        Returns:
            取得した要件定義書。

        Raises:
            NotFoundError: 要件定義書が存在しない、またはプロジェクトに属さない場合。
        """
        document = self.repository.get_by_id(db, document_id)
        if document is None or document.project_id != project_id:
            raise NotFoundError(error_messages.REQUIREMENT_DOCUMENT_NOT_FOUND)
        return document

    def update_document(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
        document_in: RequirementDocumentUpdate,
        actor_id: int | None = None,
    ) -> RequirementDocument:
        """要件定義書を更新する。

        Args:
            db: DBセッション。
            project_id: 更新対象のプロジェクトID。
            document_id: 更新対象の要件定義書ID。
            document_in: 要件定義書の更新入力値。
            actor_id: 操作ユーザーID。

        Returns:
            更新された要件定義書。

        Raises:
            NotFoundError: 要件定義書が存在しない、またはプロジェクトに属さない場合。
            VersionConflictError: versionが一致しない場合。
            DuplicateResourceError: document_codeが重複している場合。
        """
        document = self.get_document(db, project_id=project_id, document_id=document_id)
        raise_if_version_conflict(
            current_version=document.version,
            requested_version=document_in.version,
            current=RequirementDocumentRead.model_validate(document).model_dump(),
        )

        if (
            document_in.document_code is not None
            and document_in.document_code != document.document_code
            and self.repository.get_by_project_document_code(
                db,
                project_id=project_id,
                document_code=document_in.document_code,
            )
            is not None
        ):
            raise DuplicateResourceError(
                error_messages.REQUIREMENT_DOCUMENT_CODE_ALREADY_EXISTS
            )

        try:
            return self.repository.update(
                db,
                document=document,
                document_in=document_in,
                actor_id=actor_id,
            )
        except IntegrityError as exc:
            raise_duplicate_after_rollback(
                db,
                error_messages.REQUIREMENT_DOCUMENT_CODE_ALREADY_EXISTS,
                exc,
            )

    def delete_document(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
        actor_id: int | None = None,
    ) -> None:
        """要件定義書を論理削除する。

        Args:
            db: DBセッション。
            project_id: 削除対象のプロジェクトID。
            document_id: 削除対象の要件定義書ID。
            actor_id: 操作ユーザーID。

        Raises:
            NotFoundError: 要件定義書が存在しない、またはプロジェクトに属さない場合。
        """
        document = self.get_document(db, project_id=project_id, document_id=document_id)
        self.repository.soft_delete(db, document=document, actor_id=actor_id)

    def _ensure_project_exists(self, db: Session, project_id: int) -> None:
        """プロジェクトが存在することを確認する。

        Args:
            db: DBセッション。
            project_id: 存在確認対象のプロジェクトID。

        Raises:
            NotFoundError: プロジェクトが存在しない場合。
        """
        if self.project_repository.get_by_id(db, project_id) is None:
            raise NotFoundError(error_messages.PROJECT_NOT_FOUND)
