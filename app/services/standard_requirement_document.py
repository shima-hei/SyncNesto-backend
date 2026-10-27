"""標準7章・文書・履歴・再送結果を原子的に保存する。"""

import hashlib
import json

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import DuplicateResourceError, ForbiddenError
from app.models.requirement import RequirementDocument
from app.models.requirement_document_creation import RequirementDocumentCreationReceipt
from app.models.user import User
from app.repositories.requirement_document_creation import (
    RequirementDocumentCreationRepository,
)
from app.schemas.requirement import (
    RequirementSectionCreate,
    StandardRequirementDocumentCreate,
)
from app.services.authorization import AuthorizationService
from app.services.requirement_document import RequirementDocumentService
from app.services.requirement_section import RequirementSectionService

STANDARD_SECTIONS_V1 = (
    ("背景・目的", "overview"),
    ("対象範囲", "scope"),
    ("利用者・業務", "business"),
    ("機能要件", "functional"),
    ("非機能要件", "non_functional"),
    ("受け入れ条件", "acceptance"),
    ("未決事項・制約", "open_issue"),
)


class CreationKeyConflictError(DuplicateResourceError):
    """同じ送信を異なる内容へ付け替えない。"""

    code = "REQUIREMENT_DOCUMENT_CREATION_KEY_CONFLICT"
    message = error_messages.REQUIREMENT_DOCUMENT_CREATION_KEY_CONFLICT


class CreationTargetDeletedError(DuplicateResourceError):
    """削除された作成結果を復活させない。"""

    code = "REQUIREMENT_DOCUMENT_CREATION_TARGET_DELETED"
    message = error_messages.REQUIREMENT_DOCUMENT_CREATION_TARGET_DELETED


class StandardRequirementDocumentService:
    """既存の作成APIと再送台帳は変更しない。"""

    def __init__(self):
        self.documents = RequirementDocumentService()
        self.sections = RequirementSectionService()
        self.receipts = RequirementDocumentCreationRepository()

    @staticmethod
    def _authorize(db: Session, actor: User, project_id: int) -> None:
        for permission in ("requirement:create", "requirement:update"):
            if not actor.is_active or not AuthorizationService().has_project_permission(
                db, user=actor, project_id=project_id, permission_code=permission
            ):
                raise ForbiddenError()

    def _replay(
        self, db: Session, receipt: RequirementDocumentCreationReceipt, digest: str
    ) -> RequirementDocument:
        if receipt.request_hash != digest:
            raise CreationKeyConflictError()
        document = (
            self.documents.repository.get_by_id(db, receipt.document_id)
            if receipt.document_id
            else None
        )
        if document is None:
            raise CreationTargetDeletedError()
        return document

    def create(
        self,
        db: Session,
        *,
        project_id: int,
        actor: User,
        data: StandardRequirementDocumentCreate,
    ) -> tuple[RequirementDocument, bool]:
        """一意制約の競合時はrollback後の新しいtransactionで再認可する。"""
        self._authorize(db, actor, project_id)
        payload = data.model_dump(mode="json", exclude={"request_key"})
        digest = hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()
        receipt = self.receipts.find(
            db, project_id=project_id, actor_id=actor.id, request_key=data.request_key
        )
        if receipt:
            return self._replay(db, receipt, digest), True
        try:
            document = self.documents.create_document(
                db,
                project_id=project_id,
                document_in=data,
                actor_id=actor.id,
                commit=False,
            )
            for index, (title, kind) in enumerate(STANDARD_SECTIONS_V1, start=1):
                self.sections.create_section(
                    db,
                    project_id=project_id,
                    document_id=document.id,
                    section_in=RequirementSectionCreate(
                        title=title,
                        section_type=kind,
                        content=None,
                        sort_order=index * 10,
                        status="draft",
                    ),
                    actor_id=actor.id,
                    commit=False,
                )
            self.receipts.create(
                db,
                project_id=project_id,
                actor_id=actor.id,
                request_key=data.request_key,
                request_hash=digest,
                document_id=document.id,
            )
            db.commit()
            db.refresh(document)
            return document, False
        except (IntegrityError, DuplicateResourceError):
            db.rollback()
            self._authorize(db, actor, project_id)
            receipt = self.receipts.find(
                db,
                project_id=project_id,
                actor_id=actor.id,
                request_key=data.request_key,
            )
            if receipt:
                return self._replay(db, receipt, digest), True
            raise
        except Exception:
            db.rollback()
            raise
