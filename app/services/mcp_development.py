"""文書MCPの業務処理。既存Serviceの版・関連・履歴・監査を再利用する。"""

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, VersionConflictError
from app.models.user import User
from app.schemas.document import DocumentUpdate
from app.schemas.requirement import RequirementDocumentUpdate
from app.services.document import DocumentService
from app.services.mcp_document_content import body_metadata, edited_body, read_content
from app.services.mcp_operations import summary
from app.services.requirement_document import RequirementDocumentService


class McpDevelopmentService:
    """外側のMCP transactionと再送receiptの内側でのみ実行する。"""

    def read(self, db: Session, user: User, name: str, data) -> dict:
        """本文は個別取得だけに含め、関連先の権限を既存Serviceで確認する。"""
        from syncnesto_mcp.contracts import READ_CONTEXT_TOOLS

        if name in READ_CONTEXT_TOOLS:
            from app.services.mcp_read import McpReadService

            return McpReadService().read(db, user, name, data)
        service = DocumentService()
        if name == "list_project_documents":
            return service.list(
                db, data.project_id, data.page, data.page_size, data.q
            ).model_dump(mode="json")
        if name == "get_project_document":
            row = service.get(db, data.project_id, data.document_id)
            return {
                **summary(row),
                "body": row.body,
                "links": self.links(service, db, data.project_id, row.id, user),
            }
        if name == "get_project_document_content":
            row = service.get(db, data.project_id, data.document_id)
            if data.version is not None and row.version != data.version:
                raise VersionConflictError(summary(row))
            return {**summary(row), **read_content(row.body, data)}
        raise NotFoundError()

    def write(self, db: Session, user: User, name: str, data) -> dict:
        """通常APIと同じ更新・版保存を使い、結果に長い本文を含めない。"""
        service = DocumentService()
        if name == "create_project_document":
            return summary(service.create(db, data.project_id, data.data, user.id))
        if name == "update_project_document":
            return summary(
                service.update(
                    db, data.project_id, data.document_id, data.data, user.id
                )
            )
        if name == "patch_project_document":
            row = service.get(db, data.project_id, data.document_id, lock=True)
            if row.version != data.version:
                raise VersionConflictError(summary(row))
            version = row.version
            updated = service.update(
                db,
                data.project_id,
                row.id,
                DocumentUpdate(
                    version=version,
                    title=data.title if data.title is not None else row.title,
                    body=edited_body(row.body, data.edits),
                ),
                user.id,
            )
            return {
                **summary(updated),
                **body_metadata(updated.body),
                "changed": updated.version != version,
            }
        if name == "link_project_document":
            service.add_link(db, data.project_id, data.document_id, data.data, user)
            return {
                "items": self.links(
                    service, db, data.project_id, data.document_id, user
                )
            }
        if name == "set_requirement_document_objectives":
            return summary(
                RequirementDocumentService().update_document(
                    db,
                    project_id=data.project_id,
                    document_id=data.document_id,
                    document_in=RequirementDocumentUpdate(
                        version=data.version, **data.data.model_dump(exclude_unset=True)
                    ),
                    actor_id=user.id,
                )
            )
        raise NotFoundError()

    def links(
        self,
        service: DocumentService,
        db: Session,
        project_id: int,
        document_id: int,
        user: User,
    ) -> list[dict]:
        """不可視の関連先はID・件数も返さず、見える参照だけを返す。"""
        return [
            row.model_dump(mode="json")
            for row in service.links(db, project_id, document_id, user)
            if row.title is not None
        ]
