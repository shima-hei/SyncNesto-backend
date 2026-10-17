"""固定本文・添付bytes・非採用コピーの回収を、明示した状態で管理する。"""

import hashlib
import logging
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, TypedDict, TypeVar
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import (
    AppError,
    BadRequestError,
    ConflictError,
    DuplicateResourceError,
    NotFoundError,
    VersionConflictError,
)
from app.models.artifact_preservation import (
    ArtifactPreservation,
    ArtifactPreservationObject,
)
from app.models.document import DocumentAttachment, ProjectDocument
from app.repositories.artifact_preservation import ArtifactPreservationRepository
from app.schemas.artifact_preservation import (
    PreservationCreateRequest,
    PreservationDownload,
    PreservationFile,
    PreservationList,
    PreservationPreview,
    PreservationPreviewRequest,
    PreservationRead,
)
from app.services.artifact_release import ArtifactReleaseService
from app.services.artifact_release_snapshot import digest
from app.services.audit_log import AuditLogService
from app.services.document_attachment import DocumentAttachmentService
from app.services.storage import StorageService

logger = logging.getLogger(__name__)
LEASE_SECONDS = 120
T = TypeVar("T")


class PreservationClaim(TypedDict):
    """ネットワーク処理へ渡す確定済みの予約。"""

    id: UUID
    key: str
    token: UUID
    slot: int
    source: dict[str, Any]


def retry_transaction(operation: Callable[[], T]) -> T:
    """DB競合だけを新しいtransactionで再試行する。Storage操作は繰り返さない。"""
    for attempt in range(3):
        try:
            return operation()
        except DBAPIError as exc:
            code = getattr(exc.orig, "pgcode", None)
            constraint = getattr(
                getattr(exc.orig, "diag", None), "constraint_name", None
            )
            if code not in {"40001", "40P01"} and not (
                code == "23505" and constraint == "uq_artifact_preservation_request"
            ):
                raise
            if attempt == 2:
                raise ConflictError(error_messages.PRESERVATION_BUSY) from None
    raise AssertionError("unreachable")


class ArtifactPreservationService:
    """1回に1ファイルだけ進め、DBとStorageの部分失敗を台帳へ残す。"""

    def __init__(self, storage: StorageService | None = None):
        """既存の認可・固定版・Storageを再利用する。"""
        self.storage = storage
        self.releases = ArtifactReleaseService()
        self.repository = ArtifactPreservationRepository()

    def _storage(self) -> StorageService:
        """明示したStorageを優先し、実処理時だけ接続先を作る。"""
        return self.storage or StorageService(request_timeout_seconds=5)

    @contextmanager
    def _access(self, original, user, project_id, release_id, *, write=False):
        """現在の本人・全source種類・資料権限を同じ読取境界で確認する。"""
        with self.releases._transaction(
            original, user, project_id, "create" if write else "read"
        ) as (db, actor):
            release = self.releases._get(db, actor, project_id, release_id)
            self.releases._permission(db, actor, project_id, "document:read")
            if write:
                self.releases._permission(db, actor, project_id, "document:update")
            yield db, actor, release

    def _source(self, db, project_id, document_id, attachment_id) -> dict[str, Any]:
        """元所属の確認を先に行い、別案件の属性を返さない。"""
        result = db.execute(
            select(DocumentAttachment, ProjectDocument)
            .join(ProjectDocument, ProjectDocument.id == DocumentAttachment.document_id)
            .where(
                DocumentAttachment.id == attachment_id,
                DocumentAttachment.document_id == document_id,
                DocumentAttachment.deleted_at.is_(None),
                ProjectDocument.deleted_at.is_(None),
                ProjectDocument.project_id == project_id,
            )
        ).first()
        if result is None:
            raise NotFoundError()
        attachment, document = result
        # メタデータの検証にStorageクライアントは必要ない。
        DocumentAttachmentService.validate_metadata(
            attachment.filename, attachment.content_type, attachment.byte_size
        )
        return dict(
            document_id=document.id,
            attachment_id=str(attachment.id),
            document_version=document.version,
            filename=attachment.filename,
            content_type=attachment.content_type,
            byte_size=attachment.byte_size,
            source_key=attachment.storage_key,
            source_created_at=attachment.created_at.isoformat(),
        )

    def _plan(self, db, project_id, release, data) -> tuple[list[dict[str, Any]], str]:
        """元キーはhashへ含めるが、preview応答へは出さない。"""
        selection = [
            self._source(db, project_id, item.document_id, item.attachment_id)
            for item in data.attachments
        ]
        plan = digest(
            {
                "schema_version": 1,
                "release_id": str(release.id),
                "body_digest": release.content_digest,
                "selection": selection,
            }
        )
        return selection, plan

    @staticmethod
    def _file(identity, slot, source, obj=None):
        """公開可能と自動判定せず、内部向けの限定した表示属性を返す。"""
        return PreservationFile(
            file_id=uuid5(identity, str(slot)),
            **{
                k: source[k]
                for k in (
                    "document_id",
                    "attachment_id",
                    "document_version",
                    "filename",
                    "content_type",
                    "byte_size",
                )
            },
            copied=obj is not None,
            content_digest=obj.content_digest if obj else None,
            preserved_at=obj.verified_at if obj else None,
        )

    def preview(
        self, original, user, project_id, release_id, data: PreservationPreviewRequest
    ):
        """コピーも業務保存もせず、選択を確認する。"""
        with self._access(original, user, project_id, release_id, write=True) as (
            db,
            actor,
            release,
        ):
            selection, plan = self._plan(db, project_id, release, data)
            return PreservationPreview(
                release_id=release.id,
                plan_digest=plan,
                files=[
                    self._file(release.id, i, source)
                    for i, source in enumerate(selection)
                ],
                total_bytes=sum(s["byte_size"] for s in selection),
            )

    def create(
        self, original, user, project_id, release_id, data: PreservationCreateRequest
    ):
        """確認済み選択を冪等に予約する。この時点ではStorageへ書かない。"""

        def save():
            with self._access(original, user, project_id, release_id, write=True) as (
                db,
                actor,
                release,
            ):
                old = self.repository.by_request(
                    db, release_id, actor.id, data.request_id
                )
                request_hash = digest(data.model_dump(mode="json"))
                if old is not None:
                    if old.request_digest != request_hash:
                        raise DuplicateResourceError()
                    return self._read(db, old), False
                selection, plan = self._plan(db, project_id, release, data)
                if plan != data.expected_plan_digest:
                    raise VersionConflictError({"plan_digest": plan})
                row = ArtifactPreservation(
                    id=uuid4(),
                    release_id=release.id,
                    created_by=actor.id,
                    request_id=data.request_id,
                    request_digest=request_hash,
                    plan_digest=plan,
                    selection=selection,
                )
                db.add(row)
                db.flush()
                self._audit(db, actor.id, project_id, row, "reserved")
                return self._read(db, row), True

        return retry_transaction(save)

    def _read(self, db, row):
        """内部キー・lease・例外の生文字列を返さない。"""
        objects = self.repository.objects(db, row.id)
        selected = {item.slot: item for item in objects if item.selected}
        pending = sum(
            1
            for item in objects
            if (not item.selected or row.status == "cancelled")
            and (item.state == "abandoned" or row.status == "cancelled")
            and (item.cleanup_checked_at is None or item.cleanup_error)
        )
        return PreservationRead(
            id=row.id,
            release_id=row.release_id,
            status=row.status,
            created_by=row.created_by,
            created_at=row.created_at,
            completed_at=row.completed_at,
            plan_digest=row.plan_digest,
            manifest_digest=row.manifest_digest,
            error_code=row.error_code,
            files=[
                self._file(row.id, i, source, selected.get(i))
                for i, source in enumerate(row.selection)
            ],
            copied_count=len(selected),
            cleanup_pending=pending,
        )

    def _get(self, db, release_id, preservation_id, *, lock=False):
        """同じ固定本文に属する構成だけを返す。"""
        row = self.repository.get(db, release_id, preservation_id, lock=lock)
        if row is None:
            raise NotFoundError()
        return row

    def get(self, original, user, project_id, release_id, preservation_id):
        """毎回現在権限を適用して状態を返す。"""
        with self._access(original, user, project_id, release_id) as (
            db,
            actor,
            release,
        ):
            return self._read(db, self._get(db, release.id, preservation_id))

    def list(self, original, user, project_id, release_id, page, page_size):
        """本文と資料を読める範囲で件数と一覧を返す。"""
        with self._access(original, user, project_id, release_id) as (
            db,
            actor,
            release,
        ):
            query = select(ArtifactPreservation).where(
                ArtifactPreservation.release_id == release.id
            )
            total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = db.scalars(
                query.order_by(
                    ArtifactPreservation.created_at.desc(),
                    ArtifactPreservation.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            return PreservationList(
                items=[self._read(db, row) for row in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

    def _claim(
        self, original, user, project_id, release_id, preservation_id
    ) -> PreservationClaim | None:
        """ネットワーク処理より先にleaseと専用キーを確定する。"""
        with self._access(original, user, project_id, release_id, write=True) as (
            db,
            actor,
            release,
        ):
            row = self._get(db, release.id, preservation_id, lock=True)
            if row.status == "ready":
                return None
            if row.status == "cancelled":
                raise ConflictError(error_messages.PRESERVATION_CANCELLED)
            now = datetime.now(UTC)
            if row.lease_expires_at is not None and row.lease_expires_at > now:
                raise ConflictError(error_messages.PRESERVATION_BUSY)
            objects = self.repository.objects(db, row.id)
            for old in objects:
                if old.state == "reserved":
                    old.state = "abandoned"
            selected = {item.slot for item in objects if item.selected}
            slot = next(i for i in range(len(row.selection)) if i not in selected)
            source = row.selection[slot]
            if (
                self._source(
                    db, project_id, source["document_id"], UUID(source["attachment_id"])
                )
                != source
            ):
                raise VersionConflictError({"selection_changed": True})
            token, object_id = uuid4(), uuid4()
            row.status, row.error_code = "preparing", None
            row.lease_id, row.lease_expires_at = (
                token,
                now + timedelta(seconds=LEASE_SECONDS),
            )
            db.flush()
            key = (
                f"artifact-preservations/{db.info['tenant_id']}/{project_id}/"
                f"{row.id}/{object_id}"
            )
            db.add(
                ArtifactPreservationObject(
                    id=object_id,
                    preservation_id=row.id,
                    slot=slot,
                    lease_id=token,
                    storage_key=key,
                )
            )
            db.flush()
            return PreservationClaim(
                id=object_id, key=key, token=token, slot=slot, source=dict(source)
            )

    def advance(self, original, user, project_id, release_id, preservation_id):
        """最大20MiBの1ファイルを検証し、全件完了時だけmanifestを封印する。"""
        claim = retry_transaction(
            lambda: self._claim(original, user, project_id, release_id, preservation_id)
        )
        if claim is None:
            return self.get(original, user, project_id, release_id, preservation_id)
        source = claim["source"]
        try:
            storage = self._storage()
            content = storage.read_uploaded_object(
                key=source["source_key"],
                content_type=source["content_type"],
                byte_size=source["byte_size"],
            )
            DocumentAttachmentService(storage).validate_content(
                source["filename"], source["content_type"], content
            )
            content_hash = hashlib.sha256(content).hexdigest()
            storage.upload_private_object(
                key=claim["key"], content=content, content_type=source["content_type"]
            )
            del content
            preserved = storage.read_uploaded_object(
                key=claim["key"],
                content_type=source["content_type"],
                byte_size=source["byte_size"],
            )
            if hashlib.sha256(preserved).hexdigest() != content_hash:
                raise BadRequestError(error_messages.FILE_UPLOAD_MISMATCH)
            del preserved
            retry_transaction(
                lambda: self._publish(
                    original,
                    user,
                    project_id,
                    release_id,
                    preservation_id,
                    claim,
                    content_hash,
                )
            )
        except Exception as exc:
            self._fail(
                original,
                release_id,
                preservation_id,
                claim,
                "SOURCE_CHANGED"
                if isinstance(exc, AppError)
                else "STORAGE_COPY_FAILED",
            )
            self._cleanup_one(original, release_id, preservation_id, claim["id"])
            if isinstance(exc, AppError):
                raise
            logger.warning(
                "Artifact copy failed: exception_type=%s", type(exc).__name__
            )
        return self.get(original, user, project_id, release_id, preservation_id)

    def _publish(
        self,
        original,
        user,
        project_id,
        release_id,
        preservation_id,
        claim,
        content_hash,
    ):
        """コピー後の新しい認可・元状態・leaseで採用を判定する。"""
        with self._access(original, user, project_id, release_id, write=True) as (
            db,
            actor,
            release,
        ):
            row = self._get(db, release.id, preservation_id, lock=True)
            if (
                row.status != "preparing"
                or row.lease_id != claim["token"]
                or row.lease_expires_at is None
                or row.lease_expires_at <= datetime.now(UTC)
            ):
                raise ConflictError(error_messages.PRESERVATION_BUSY)
            source = claim["source"]
            if (
                self._source(
                    db, project_id, source["document_id"], UUID(source["attachment_id"])
                )
                != source
            ):
                raise VersionConflictError({"selection_changed": True})
            obj = db.get(ArtifactPreservationObject, claim["id"])
            assert obj is not None
            obj.state, obj.selected = "verified", True
            obj.content_digest, obj.byte_size, obj.verified_at = (
                content_hash,
                source["byte_size"],
                datetime.now(UTC),
            )
            db.flush()
            objects = [
                item for item in self.repository.objects(db, row.id) if item.selected
            ]
            row.lease_id, row.lease_expires_at = None, None
            if len(objects) == len(row.selection):
                by_slot = {item.slot: item for item in objects}
                manifest = {
                    "schema_version": 1,
                    "release_id": str(release.id),
                    "body_content_digest": release.content_digest,
                    "files": [
                        {
                            **{
                                k: source[k]
                                for k in (
                                    "document_id",
                                    "attachment_id",
                                    "document_version",
                                    "filename",
                                    "content_type",
                                    "byte_size",
                                )
                            },
                            "content_digest": by_slot[i].content_digest,
                        }
                        for i, source in enumerate(row.selection)
                    ],
                }
                row.manifest, row.manifest_digest = manifest, digest(manifest)
                row.status, row.completed_at = "ready", datetime.now(UTC)
                self._audit(db, actor.id, project_id, row, "completed")
            db.flush()

    @contextmanager
    def _ledger(self, original):
        """開始済み処理の失敗記録だけを、同じtenantの内部transactionで行う。"""
        with (
            Session(
                bind=original.get_bind().engine,
                info={"tenant_id": original.info["tenant_id"]},
            ) as db,
            db.begin(),
        ):
            yield db

    def _fail(self, original, release_id, preservation_id, claim, error_code):
        """認可失効時でも未採用キーの台帳を失わない。権限付与や採用は行わない。"""

        def mark():
            with self._ledger(original) as db:
                row = self._get(db, release_id, preservation_id, lock=True)
                obj = db.get(ArtifactPreservationObject, claim["id"])
                if obj is not None and not obj.selected:
                    obj.state = "abandoned"
                    obj.cleanup_checked_at = None
                if row.status == "preparing" and row.lease_id == claim["token"]:
                    row.status, row.error_code = "failed", error_code
                    row.lease_id, row.lease_expires_at = None, None

        try:
            retry_transaction(mark)
        except Exception:
            logger.warning("Artifact failure ledger requires retry")

    def _cleanup_one(self, original, release_id, preservation_id, object_id):
        """取消または非採用が確定した予約キーだけを回収する。台帳は残す。"""
        try:
            with self._ledger(original) as db:
                row = self._get(db, release_id, preservation_id, lock=True)
                obj = db.get(ArtifactPreservationObject, object_id)
                if (
                    obj is None
                    or obj.preservation_id != row.id
                    or not (
                        row.status == "cancelled"
                        or (not obj.selected and obj.state == "abandoned")
                    )
                ):
                    return
                key = obj.storage_key
            failed = False
            try:
                self._storage().delete_object(key)
            except Exception:
                failed = True
            with self._ledger(original) as db:
                row = self._get(db, release_id, preservation_id, lock=True)
                obj = db.get(ArtifactPreservationObject, object_id)
                if obj is not None:
                    obj.cleanup_error, obj.cleanup_checked_at = (
                        failed,
                        datetime.now(UTC),
                    )
        except Exception:
            logger.warning("Artifact object cleanup requires retry")

    def cleanup(
        self, original, user, project_id, release_id, preservation_id, cancel=False
    ):
        """明示取消後のコピーや非採用試行を回収し、完成実体は保持する。"""

        def mark():
            with self._access(original, user, project_id, release_id, write=True) as (
                db,
                actor,
                release,
            ):
                row = self._get(db, release.id, preservation_id, lock=True)
                if cancel:
                    if row.status == "ready":
                        raise ConflictError(error_messages.PRESERVATION_IMMUTABLE)
                    if row.status != "cancelled":
                        row.status, row.lease_id, row.lease_expires_at = (
                            "cancelled",
                            None,
                            None,
                        )
                        self._audit(db, actor.id, project_id, row, "cancelled")
                now = datetime.now(UTC)
                objects = self.repository.objects(db, row.id)
                for obj in objects:
                    if (
                        not obj.selected
                        and obj.state == "reserved"
                        and (
                            row.lease_expires_at is None or row.lease_expires_at <= now
                        )
                    ):
                        obj.state = "abandoned"
                return [
                    obj.id
                    for obj in objects
                    if row.status == "cancelled"
                    or (not obj.selected and obj.state == "abandoned")
                ]

        ids = retry_transaction(mark)
        for object_id in ids:
            self._cleanup_one(original, release_id, preservation_id, object_id)
        return self.get(original, user, project_id, release_id, preservation_id)

    def download(
        self, original, user, project_id, release_id, preservation_id, file_id
    ):
        """完成manifestの採用実体だけを、最大60秒のURLで取得する。"""
        with self._access(original, user, project_id, release_id) as (
            db,
            actor,
            release,
        ):
            row = self._get(db, release.id, preservation_id)
            if row.status != "ready":
                raise ConflictError(error_messages.PRESERVATION_NOT_READY)
            slot = next(
                (
                    i
                    for i in range(len(row.selection))
                    if uuid5(row.id, str(i)) == file_id
                ),
                None,
            )
            if slot is None:
                raise NotFoundError()
            obj = next(
                (
                    o
                    for o in self.repository.objects(db, row.id)
                    if o.slot == slot and o.selected
                ),
                None,
            )
            if obj is None:
                raise NotFoundError()
            url = self._storage().private_object_url(
                obj.storage_key,
                download_filename=row.selection[slot]["filename"],
                expires_in=60,
            )
            return PreservationDownload(url=url, expires_in=60)

    @staticmethod
    def _audit(db, actor_id, project_id, row, event):
        """監査は識別子だけにし、本文・元キー・署名URLを残さない。"""
        AuditLogService().record(
            db,
            event_type=f"artifact_preservation.{event}",
            actor_user_id=actor_id,
            project_id=project_id,
            resource_type="artifact_preservation",
            metadata={
                "preservation_id": str(row.id),
                "release_id": str(row.release_id),
            },
            commit=False,
        )
