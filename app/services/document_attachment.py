"""文書の添付を既存の直接送信と非公開ストレージで管理する。"""

import logging
from datetime import UTC, datetime
from pathlib import PurePath
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.document import DocumentAttachment
from app.schemas.file_upload import FileUploadPlan, FileUploadRequest
from app.services.document import DocumentService
from app.services.file_upload import FileUploadService
from app.services.storage import StorageService

logger = logging.getLogger(__name__)
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
FILE_TYPES = {
    "application/pdf": ((".pdf",), b"%PDF-"),
    "image/png": ((".png",), b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": ((".jpg", ".jpeg"), b"\xff\xd8\xff"),
    "image/webp": ((".webp",), b"RIFF"),
    "text/plain": ((".txt", ".md", ".csv"), b""),
    "application/json": ((".json",), b""),
}


class DocumentAttachmentService:
    """登録済みファイルを再上書きできない保存先へ確定する。"""

    def __init__(self, storage: StorageService | None = None) -> None:
        """共通ストレージと文書の所有検証を使う。"""
        self.storage = storage or StorageService()
        self.documents = DocumentService()
        self.repository = self.documents.repository

    def validate_metadata(self, filename: str, content_type: str, size: int) -> str:
        """形式、拡張子、容量と安全な表示名を確認する。"""
        name = PurePath(filename.replace("\\", "/")).name.strip()
        spec = FILE_TYPES.get(content_type)
        if (
            not name
            or len(name) > 255
            or any(ord(char) < 32 or ord(char) == 127 for char in name)
            or not 0 < size <= MAX_DOCUMENT_BYTES
            or spec is None
            or not name.lower().endswith(spec[0])
        ):
            raise BadRequestError(error_messages.DOCUMENT_FILE_INVALID)
        return name

    def validate_content(self, filename: str, content_type: str, content: bytes) -> str:
        """メタデータだけでなく確定前に内容も検証する。"""
        name = self.validate_metadata(filename, content_type, len(content))
        signature = FILE_TYPES[content_type][1]
        if signature and not content.startswith(signature):
            raise BadRequestError(error_messages.DOCUMENT_FILE_INVALID)
        if content_type == "image/webp" and content[8:12] != b"WEBP":
            raise BadRequestError(error_messages.DOCUMENT_FILE_INVALID)
        if content_type in {"text/plain", "application/json"}:
            try:
                content.decode("utf-8")
            except UnicodeDecodeError:
                raise BadRequestError(error_messages.DOCUMENT_FILE_INVALID) from None
        return name

    def plan(
        self,
        db: Session,
        project_id: int,
        document_id: int,
        data: FileUploadRequest,
        actor_id: int,
    ) -> FileUploadPlan:
        """文書の所有と件数を検証して送信を予約する。"""
        self.documents.get(db, project_id, document_id, lock=True)
        self.validate_metadata(data.filename, data.content_type, data.byte_size)
        self.check_count(db, document_id)
        return FileUploadService(self.storage.for_demo(db)).plan(
            data, user_id=actor_id, scope=f"document:{project_id}:{document_id}"
        )

    def check_count(self, db: Session, document_id: int) -> None:
        """同じ文書をロックした状態で20件上限を確認する。"""
        if len(self.repository.attachments(db, document_id)) >= 20:
            raise BadRequestError(error_messages.DOCUMENT_ATTACHMENT_LIMIT)

    def complete(
        self, db: Session, project_id: int, document_id: int, token: str, actor_id: int
    ) -> DocumentAttachment:
        """許可の本人・用途・期限と一時ファイルを検証する。"""
        self.documents.get(db, project_id, document_id, lock=True)
        storage = self.storage.for_demo(db)
        uploads = FileUploadService(storage)
        upload_id, metadata, key = uploads.verify(
            token, user_id=actor_id, scope=f"document:{project_id}:{document_id}"
        )
        existing = self.repository.attachment(db, document_id, upload_id)
        if existing is not None:
            if existing.deleted_at is not None:
                raise NotFoundError(error_messages.DOCUMENT_NOT_FOUND)
            return existing
        try:
            content = storage.read_uploaded_object(
                key=key,
                content_type=metadata.content_type,
                byte_size=metadata.byte_size,
            )
            return self.upload(
                db,
                project_id,
                document_id,
                metadata.filename,
                metadata.content_type,
                content,
                actor_id,
                attachment_id=upload_id,
            )
        finally:
            uploads.discard(key)

    def upload(
        self,
        db: Session,
        project_id: int,
        document_id: int,
        filename: str,
        content_type: str,
        content: bytes,
        actor_id: int,
        *,
        attachment_id: UUID | None = None,
    ) -> DocumentAttachment:
        """内容を検証し、S3保存とDB登録の失敗後も回収可能にする。"""
        document = self.documents.get(db, project_id, document_id, lock=True)
        name = self.validate_content(filename, content_type, content)
        self.check_count(db, document_id)
        storage = self.storage.for_demo(db)
        row_id = attachment_id or uuid4()
        key = storage.object_key(
            f"projects/{project_id}/documents/{document_id}/{row_id}/{uuid4()}"
        )
        storage.upload_private_object(
            key=key, content=content, content_type=content_type
        )
        try:
            # デモの予約commit後、文書削除との競合も再検証する。
            document = self.documents.get(db, project_id, document_id, lock=True)
            existing = self.repository.attachment(db, document_id, row_id)
            if existing is not None:
                # 同時確定で先に保存されたファイルへ触れず、今回のコピーだけを破棄する。
                storage.delete_object(key)
                if existing.deleted_at is not None:
                    raise NotFoundError(error_messages.DOCUMENT_NOT_FOUND)
                return existing
            self.check_count(db, document_id)
            row = DocumentAttachment(
                id=row_id,
                document_id=document_id,
                filename=name,
                content_type=content_type,
                byte_size=len(content),
                storage_key=key,
                uploaded_by=actor_id,
            )
            self.repository.add_attachment(db, row)
            db.commit()
            db.refresh(row)
        except Exception:
            db.rollback()
            try:
                storage.delete_object(key)
            except Exception:
                logger.warning("Document upload cleanup requires retry")
            raise
        self.documents.record(db, document, actor_id, "attachment_added")
        return row

    def get(
        self,
        db: Session,
        project_id: int,
        document_id: int,
        attachment_id: UUID,
        *,
        lock: bool = False,
    ) -> DocumentAttachment:
        """別文書と削除された文書・添付の取得を拒否する。"""
        self.documents.get(db, project_id, document_id, lock=lock)
        row = self.repository.attachment(db, document_id, attachment_id)
        if row is None or row.deleted_at is not None:
            raise NotFoundError(error_messages.DOCUMENT_NOT_FOUND)
        return row

    def download(
        self, db: Session, project_id: int, document_id: int, attachment_id: UUID
    ) -> str:
        """認可後に、強制download用の短期署名URLを発行する。"""
        row = self.get(db, project_id, document_id, attachment_id)
        return self.storage.for_demo(db).private_object_url(
            row.storage_key, download_filename=row.filename
        )

    def delete(
        self,
        db: Session,
        project_id: int,
        document_id: int,
        attachment_id: UUID,
        actor_id: int,
    ) -> None:
        """論理削除し、以後の署名URL発行を拒否する。"""
        row = self.get(db, project_id, document_id, attachment_id, lock=True)
        row.deleted_at = datetime.now(UTC)
        db.commit()
        document = self.documents.get(db, project_id, document_id)
        self.documents.record(db, document, actor_id, "attachment_deleted")
