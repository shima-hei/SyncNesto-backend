"""共有固定版の権限で添付の下書き・確定・配信を扱う。"""

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import func, select

from app.core.config import settings
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.models.artifact_publication_access import (
    ArtifactPublicationGrant,
    SharedCommentFile,
)
from app.schemas.shared_publication_comment import SharedCommentFileRead
from app.services.comment_image import (
    COMMENT_IMAGE_TYPES,
    DRAFT_RETENTION,
    MAX_COMMENT_IMAGE_BYTES,
    MAX_DRAFT_IMAGES_PER_USER,
)
from app.services.document_attachment import DocumentAttachmentService
from app.services.file_upload import FileUploadService
from app.services.publication_access_context import retry_publication_operation
from app.services.storage import StorageService


@dataclass(frozen=True)
class SharedCommentAccess:
    can_post: bool
    author: str
    expires_at: datetime | None = None
    grant_id: UUID | None = None
    revision: str | None = None
    download_allowed: bool = True


class SharedCommentFiles:
    def __init__(self, context, access):
        self.context = context
        self.access = access
        self.storage_factory = StorageService

    @contextmanager
    def authorized(self, original, user, publication_id, project_id, *, write=False):
        with self.context.transaction(original, user, project_id, write=write) as (
            db,
            actor,
        ):
            allowed = self.access(
                db, original, actor, project_id, publication_id, write
            )
            if write and not allowed.can_post:
                raise ForbiddenError()
            yield db, actor, allowed

    @staticmethod
    def _metadata(filename, content_type, byte_size):
        if byte_size > MAX_COMMENT_IMAGE_BYTES:
            raise BadRequestError("添付ファイルは1件につき3 MiB以下にしてください。")
        return DocumentAttachmentService.validate_metadata(
            filename, content_type, byte_size
        )

    @staticmethod
    def _scope(publication_id, grant_id):
        return f"shared-comment-file:{publication_id}:{grant_id or 'internal'}"

    @staticmethod
    def _drafts(publication_id, actor_id):
        return select(SharedCommentFile).where(
            SharedCommentFile.publication_id == publication_id,
            SharedCommentFile.uploaded_by == actor_id,
            SharedCommentFile.comment_id.is_(None),
        )

    def _prune(self, db, publication_id, actor_id, now, storage):
        expired = db.scalars(
            self._drafts(publication_id, actor_id)
            .where(SharedCommentFile.created_at <= now - DRAFT_RETENTION)
            .order_by(SharedCommentFile.created_at, SharedCommentFile.id)
            .limit(8)
            .with_for_update(skip_locked=True)
        ).all()
        for row in expired:
            try:
                storage.delete_object(row.storage_key)
            except Exception:
                continue
            db.delete(row)

    def _existing(self, db, identity, publication_id, actor_id, grant_id):
        return db.scalar(
            select(SharedCommentFile).where(
                SharedCommentFile.id == identity,
                SharedCommentFile.publication_id == publication_id,
                SharedCommentFile.uploaded_by == actor_id,
                SharedCommentFile.grant_id == grant_id,
            )
        )

    @retry_publication_operation
    def plan(self, original, user, publication_id, data, project_id=None):
        with self.authorized(
            original, user, publication_id, project_id, write=True
        ) as (db, actor, allowed):
            self._metadata(data.filename, data.content_type, data.byte_size)
            storage = self.storage_factory()
            now = db.scalar(select(func.clock_timestamp()))
            self._prune(db, publication_id, actor.id, now, storage)
            return FileUploadService(storage).plan(
                data,
                user_id=actor.id,
                scope=self._scope(publication_id, allowed.grant_id),
            )

    def upload(self, original, user, publication_id, file, request_id, project_id=None):
        with self.authorized(
            original, user, publication_id, project_id, write=True
        ) as (_, actor, allowed):
            if settings.file_upload_mode != "server":
                raise BadRequestError("アップロードをやり直してください。")
            identity = uuid5(
                NAMESPACE_URL,
                f"shared-file:{publication_id}:{actor.id}:{allowed.grant_id}:{request_id}",
            )
        return self._store(
            original,
            user,
            publication_id,
            project_id,
            identity,
            allowed,
            file.filename or "",
            file.content_type or "",
            file.file.read(MAX_COMMENT_IMAGE_BYTES + 1),
        )

    def complete(self, original, user, publication_id, token, project_id=None):
        with self.authorized(
            original, user, publication_id, project_id, write=True
        ) as (db, actor, allowed):
            storage = self.storage_factory()
            uploads = FileUploadService(storage)
            identity, data, key = uploads.verify(
                token,
                user_id=actor.id,
                scope=self._scope(publication_id, allowed.grant_id),
            )
            existing = self._existing(
                db, identity, publication_id, actor.id, allowed.grant_id
            )
            if existing is not None:
                return SharedCommentFileRead.model_validate(existing)
            self._metadata(data.filename, data.content_type, data.byte_size)
        try:
            content = storage.read_uploaded_object(
                key=key, content_type=data.content_type, byte_size=data.byte_size
            )
        except BadRequestError:
            with self.authorized(
                original, user, publication_id, project_id, write=True
            ) as (db, actor, current):
                existing = self._existing(
                    db, identity, publication_id, actor.id, current.grant_id
                )
                if existing is None:
                    raise
                return SharedCommentFileRead.model_validate(existing)
        try:
            return self._store(
                original,
                user,
                publication_id,
                project_id,
                identity,
                allowed,
                data.filename,
                data.content_type,
                content,
            )
        finally:
            uploads.discard(key)

    def _store(
        self,
        original,
        user,
        publication_id,
        project_id,
        identity,
        prior,
        filename,
        content_type,
        content,
    ):
        self._metadata(filename, content_type, len(content))
        name = DocumentAttachmentService.validate_content(
            filename, content_type, content
        )
        digest = sha256(content).hexdigest()
        storage = self.storage_factory()
        key = storage.object_key(
            f"shared-comment-files/{publication_id}/{identity}/{uuid4()}"
        )
        keep = False
        try:
            storage.upload_private_object(
                key=key, content=content, content_type=content_type
            )
            with self.authorized(
                original, user, publication_id, project_id, write=True
            ) as (db, actor, current):
                if current.grant_id != prior.grant_id:
                    raise ConflictError()
                if current.grant_id is not None:
                    db.scalar(
                        select(ArtifactPublicationGrant.id)
                        .where(ArtifactPublicationGrant.id == current.grant_id)
                        .with_for_update()
                    )
                existing = self._existing(
                    db, identity, publication_id, actor.id, current.grant_id
                )
                if existing is not None:
                    if (
                        existing.content_digest,
                        existing.filename,
                        existing.content_type,
                    ) != (digest, name, content_type):
                        raise ConflictError()
                    return SharedCommentFileRead.model_validate(existing)
                now = db.scalar(select(func.clock_timestamp()))
                drafts = self._drafts(publication_id, actor.id).where(
                    SharedCommentFile.created_at > now - DRAFT_RETENTION
                )
                if (
                    db.scalar(select(func.count()).select_from(drafts.subquery())) or 0
                ) >= MAX_DRAFT_IMAGES_PER_USER:
                    raise BadRequestError("未送信の添付ファイルが上限に達しています。")
                row = SharedCommentFile(
                    id=identity,
                    publication_id=publication_id,
                    uploaded_by=actor.id,
                    grant_id=current.grant_id,
                    filename=name,
                    content_type=content_type,
                    byte_size=len(content),
                    content_digest=digest,
                    storage_key=key,
                )
                db.add(row)
                db.flush()
                result = SharedCommentFileRead.model_validate(row)
                # commit の応答が途切れても、確定済み行が参照するコピーを回収しない。
                keep = True
            return result
        finally:
            if not keep:
                FileUploadService(storage).discard(key)

    @retry_publication_operation
    def discard(self, original, user, publication_id, identity, project_id=None):
        with self.authorized(
            original, user, publication_id, project_id, write=True
        ) as (db, actor, allowed):
            row = db.scalar(
                self._drafts(publication_id, actor.id)
                .where(
                    SharedCommentFile.id == identity,
                    SharedCommentFile.grant_id == allowed.grant_id,
                )
                .with_for_update()
            )
            if row is None:
                raise NotFoundError()
            self.storage_factory().delete_object(row.storage_key)
            db.delete(row)

    @staticmethod
    def list_for_comments(db, comment_ids):
        files = defaultdict(list)
        if comment_ids:
            for row in db.scalars(
                select(SharedCommentFile)
                .where(SharedCommentFile.comment_id.in_(comment_ids))
                .order_by(SharedCommentFile.comment_id, SharedCommentFile.position)
            ):
                files[row.comment_id].append(SharedCommentFileRead.model_validate(row))
        return files

    def bind(self, db, comment, actor_id, grant_id, identities):
        if not identities:
            return []
        now = db.scalar(select(func.clock_timestamp()))
        candidates = {
            row.id: row
            for row in db.scalars(
                self._drafts(comment.publication_id, actor_id)
                .where(
                    SharedCommentFile.id.in_(identities),
                    SharedCommentFile.grant_id == grant_id,
                    SharedCommentFile.created_at > now - DRAFT_RETENTION,
                )
                .order_by(SharedCommentFile.id)
                .with_for_update()
            )
        }
        if len(candidates) != len(identities):
            raise NotFoundError()
        for position, identity in enumerate(identities):
            row = candidates[identity]
            row.comment_id, row.position = comment.id, position
        return [
            SharedCommentFileRead.model_validate(candidates[identity])
            for identity in identities
        ]

    @retry_publication_operation
    def _resolve(
        self, original, user, publication_id, comment_id, file_id, project_id, inline
    ):
        with self.authorized(original, user, publication_id, project_id) as (
            db,
            _,
            allowed,
        ):
            row = db.scalar(
                select(SharedCommentFile).where(
                    SharedCommentFile.id == file_id,
                    SharedCommentFile.publication_id == publication_id,
                    SharedCommentFile.comment_id == comment_id,
                )
            )
            if (
                row is None
                or (inline and row.content_type not in COMMENT_IMAGE_TYPES)
                or (not inline and not allowed.download_allowed)
            ):
                raise NotFoundError()
            return (
                row.storage_key,
                SharedCommentFileRead.model_validate(row),
                allowed.revision,
            )

    def content(
        self,
        original,
        user,
        publication_id,
        comment_id,
        file_id,
        project_id=None,
        *,
        inline=False,
    ):
        lookup = (
            original,
            user,
            publication_id,
            comment_id,
            file_id,
            project_id,
            inline,
        )
        key, details, revision = self._resolve(*lookup)
        content = self.storage_factory().read_uploaded_object(
            key=key, content_type=details.content_type, byte_size=details.byte_size
        )
        if sha256(content).hexdigest() != details.content_digest or self._resolve(
            *lookup
        ) != (key, details, revision):
            raise ConflictError()
        return content, details
