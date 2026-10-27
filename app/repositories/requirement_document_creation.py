"""標準章付き文書の再送結果を取得・追加する。"""

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.requirement_document_creation import RequirementDocumentCreationReceipt


class RequirementDocumentCreationRepository:
    """commitは文書と章をまとめるServiceへ委ねる。"""

    def find(
        self, db: Session, *, project_id: int, actor_id: int, request_key: UUID
    ) -> RequirementDocumentCreationReceipt | None:
        """別の本人/Projectの結果は返さない。"""
        return (
            db.query(RequirementDocumentCreationReceipt)
            .filter_by(
                project_id=project_id, actor_id=actor_id, request_key=request_key
            )
            .first()
        )

    def create(
        self,
        db: Session,
        *,
        project_id: int,
        actor_id: int,
        request_key: UUID,
        request_hash: str,
        document_id: int,
    ) -> RequirementDocumentCreationReceipt:
        """結果を同じtransactionへ追加する。"""
        receipt = RequirementDocumentCreationReceipt(
            project_id=project_id,
            actor_id=actor_id,
            request_key=request_key,
            request_hash=request_hash,
            document_id=document_id,
        )
        db.add(receipt)
        db.flush()
        return receipt
