"""既存の非公開ストレージを使ってコメント画像を確定する。"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.comment_image import CommentImage
from app.models.project import Project
from app.schemas.comment_image import MAX_COMMENT_IMAGES, CommentImageKind
from app.schemas.file_upload import FileUploadPlan, FileUploadRequest
from app.services.document_attachment import DocumentAttachmentService
from app.services.file_upload import FileUploadService
from app.services.storage import StorageService

MAX_COMMENT_IMAGE_BYTES = 3 * 1024 * 1024
MAX_DRAFT_IMAGES_PER_USER = 24
COMMENT_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
DRAFT_RETENTION = timedelta(days=1)
OWNER_BY_KIND = {
    "task": "task_comment_id",
    "requirement": "requirement_comment_id",
    "specification": "specification_comment_id",
    "test_design": "test_comment_id",
}
logger = logging.getLogger(__name__)


def drafts_only():
    """投稿と並行して、使用済みの画像を破棄しない。"""
    return and_(
        *(getattr(CommentImage, key).is_(None) for key in OWNER_BY_KIND.values())
    )


class CommentImageService:
    """投稿前の画像と投稿に所属する画像を管理する。"""

    def __init__(self, storage: StorageService | None = None) -> None:
        self.storage = storage or StorageService()

    @staticmethod
    def validate_metadata(filename: str, content_type: str, byte_size: int) -> None:
        if (
            content_type not in COMMENT_IMAGE_TYPES
            or not 0 < byte_size <= MAX_COMMENT_IMAGE_BYTES
        ):
            raise BadRequestError(
                "PNG・JPEG・WebPの画像を3 MiB以下で添付してください。"
            )
        DocumentAttachmentService.validate_metadata(filename, content_type, byte_size)

    def plan(
        self,
        db: Session,
        project_id: int,
        kind: CommentImageKind,
        data: FileUploadRequest,
        actor_id: int,
    ) -> FileUploadPlan:
        self.validate_metadata(data.filename, data.content_type, data.byte_size)
        self.prune_expired_drafts(db, project_id, actor_id)
        return FileUploadService(self.storage.for_demo(db)).plan(
            data, user_id=actor_id, scope=f"comment-image:{project_id}:{kind}"
        )

    def complete(
        self,
        db: Session,
        project_id: int,
        kind: CommentImageKind,
        token: str,
        actor_id: int,
    ) -> CommentImage:
        storage = self.storage.for_demo(db)
        uploads = FileUploadService(storage)
        image_id, data, pending_key = uploads.verify(
            token, user_id=actor_id, scope=f"comment-image:{project_id}:{kind}"
        )
        existing = db.get(CommentImage, image_id)
        if existing is not None:
            return existing
        self.validate_metadata(data.filename, data.content_type, data.byte_size)
        try:
            content = storage.read_uploaded_object(
                key=pending_key,
                content_type=data.content_type,
                byte_size=data.byte_size,
            )
        except BadRequestError:
            existing = db.get(CommentImage, image_id)
            if existing is not None:
                return existing
            raise
        try:
            return self.upload(
                db,
                project_id,
                kind,
                data.filename,
                data.content_type,
                content,
                actor_id,
                image_id=image_id,
            )
        finally:
            uploads.discard(pending_key)

    def upload(
        self,
        db: Session,
        project_id: int,
        kind: CommentImageKind,
        filename: str,
        content_type: str,
        content: bytes,
        actor_id: int,
        *,
        image_id: UUID | None = None,
    ) -> CommentImage:
        self.validate_metadata(filename, content_type, len(content))
        storage = self.storage.for_demo(db)
        name = DocumentAttachmentService(storage).validate_content(
            filename, content_type, content
        )
        image_id = image_id or uuid4()
        key = storage.object_key(
            f"projects/{project_id}/comment-images/{image_id}/{uuid4()}"
        )
        storage.upload_private_object(
            key=key, content=content, content_type=content_type
        )
        try:
            # 同じ署名の並行確定を直列化し、デモの予約commit後も所属を確認する。
            project = db.scalar(
                select(Project.id)
                .where(
                    Project.id == project_id,
                    Project.deleted_at.is_(None),
                )
                .with_for_update()
            )
            if project is None:
                raise NotFoundError()
            existing = db.get(CommentImage, image_id)
            if existing is not None:
                FileUploadService(storage).discard(key)
                return existing
            draft_count = db.scalar(
                select(func.count(CommentImage.id)).where(
                    CommentImage.project_id == project_id,
                    CommentImage.uploaded_by == actor_id,
                    drafts_only(),
                )
            )
            if (draft_count or 0) >= MAX_DRAFT_IMAGES_PER_USER:
                raise BadRequestError("未送信の添付画像が上限に達しています。")
            row = CommentImage(
                id=image_id,
                project_id=project_id,
                kind=kind,
                uploaded_by=actor_id,
                filename=name,
                content_type=content_type,
                byte_size=len(content),
                storage_key=key,
            )
            db.add(row)
            db.commit()
        except Exception:
            db.rollback()
            FileUploadService(storage).discard(key)
            raise
        return row

    def get(self, db: Session, project_id: int, image_id: UUID) -> CommentImage:
        row = db.get(CommentImage, image_id)
        if row is None or row.project_id != project_id:
            raise NotFoundError()
        return row

    def content(self, db: Session, image: CommentImage) -> bytes:
        return self.storage.for_demo(db).read_uploaded_object(
            key=image.storage_key,
            content_type=image.content_type,
            byte_size=image.byte_size,
        )

    def discard_draft(
        self, db: Session, project_id: int, image_id: UUID, actor_id: int
    ) -> None:
        row = db.scalar(
            select(CommentImage)
            .where(
                CommentImage.id == image_id,
                CommentImage.project_id == project_id,
                CommentImage.uploaded_by == actor_id,
                drafts_only(),
            )
            .with_for_update()
        )
        if row is None:
            raise NotFoundError()
        self.storage.for_demo(db).delete_object(row.storage_key)
        db.delete(row)
        db.commit()

    def prune_expired_drafts(self, db: Session, project_id: int, actor_id: int) -> None:
        rows = db.scalars(
            select(CommentImage)
            .where(
                CommentImage.project_id == project_id,
                CommentImage.uploaded_by == actor_id,
                CommentImage.created_at < datetime.now(UTC) - DRAFT_RETENTION,
                drafts_only(),
            )
            .order_by(CommentImage.created_at, CommentImage.id)
            .limit(8)
            .with_for_update(skip_locked=True)
        ).all()
        if not rows:
            return
        storage = self.storage.for_demo(db)
        for row in rows:
            try:
                storage.delete_object(row.storage_key)
            except Exception:
                logger.warning("Comment image draft cleanup requires retry")
            else:
                db.delete(row)
        db.commit()


def bind_comment_images(
    db: Session,
    *,
    project_id: int,
    kind: CommentImageKind,
    images: list[CommentImage],
    actor_id: int | None,
    image_ids: list[UUID],
) -> None:
    """同じ案件で自分が添付した画像だけを、投稿と同じtransactionで一度だけ確定する。"""
    if not image_ids:
        return
    if (
        len(image_ids) > MAX_COMMENT_IMAGES
        or len(set(image_ids)) != len(image_ids)
        or actor_id is None
    ):
        raise BadRequestError(
            "同じ画像は一度だけ、1件のコメントに4枚まで添付できます。"
        )
    rows = db.scalars(
        select(CommentImage)
        .where(
            CommentImage.id.in_(image_ids),
            CommentImage.project_id == project_id,
            CommentImage.kind == kind,
            CommentImage.uploaded_by == actor_id,
            CommentImage.created_at >= datetime.now(UTC) - DRAFT_RETENTION,
            drafts_only(),
        )
        .order_by(CommentImage.id)
        .with_for_update()
    ).all()
    if len(rows) != len(image_ids):
        raise BadRequestError("添付画像を確認できません。画像を選び直してください。")
    by_id = {row.id: row for row in rows}
    images.extend(by_id[image_id] for image_id in image_ids)
    db.flush()
