"""内部の現在権限を確認して公開準備版を保存・閲覧する。"""

from contextlib import contextmanager
from typing import cast
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.core import error_messages
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    DuplicateResourceError,
    ForbiddenError,
    NotFoundError,
    VersionConflictError,
)
from app.models.artifact_preservation import ArtifactPreservation
from app.models.artifact_publication import ArtifactPublication
from app.models.artifact_release import ArtifactReleaseNode
from app.repositories.artifact_publication import ArtifactPublicationRepository
from app.schemas.artifact_publication import (
    PublicationContentMetadata,
    PublicationInternalOrigin,
    PublicationList,
    PublicationNodePage,
    PublicationNodeRead,
    PublicationPreview,
    PublicationRead,
    PublicationSummary,
    PublicProjection,
)
from app.services.artifact_publication_projection import NAMESPACE, build_projection
from app.services.artifact_release import SOURCE_PERMISSIONS, ArtifactReleaseService
from app.services.artifact_release_snapshot import MAX_PAGE_BYTES, digest
from app.services.audit_log import AuditLogService
from app.services.fixed_approval_cursor import decode_cursor, encode_cursor

PUBLICATION_WRITES_ENABLED = True


class ArtifactPublicationService:
    """ゲスト公開・ダウンロード経路を増やさず準備版を扱う。"""

    def __init__(self):
        """既存の内部専用transaction・認可を再利用する。"""
        self.releases = ArtifactReleaseService()
        self.repository = ArtifactPublicationRepository()

    @contextmanager
    def access(self, original, user, project_id, *, write=False):
        """現在session/MFA/組織/Project所属を再検証する。"""
        if write and not PUBLICATION_WRITES_ENABLED:
            raise ForbiddenError()
        with self.releases._transaction(original, user, project_id, "read") as (
            db,
            actor,
        ):
            self.releases._permission(
                db, actor, project_id, "artifact_publication:read"
            )
            if write:
                self.releases._permission(
                    db, actor, project_id, "artifact_publication:manage"
                )
                self.releases._permission(
                    db, actor, project_id, "artifact_release:create"
                )
            yield db, actor

    def sources(self, db, actor, project_id, data):
        """封印済み本文と、同じ本文の完成した添付構成を確認する。"""
        release = self.releases._get(db, actor, project_id, data.release_id)
        if data.projection_version != release.projection_version.replace(
            "internal-", "public-", 1
        ):
            raise BadRequestError(error_messages.OBJECTIVE_PROJECTION_UPGRADE)
        preservation = None
        if data.preservation_id is not None:
            self.releases._permission(db, actor, project_id, "document:read")
            preservation = db.scalar(
                select(ArtifactPreservation).where(
                    ArtifactPreservation.id == data.preservation_id,
                    ArtifactPreservation.release_id == release.id,
                )
            )
            if preservation is None:
                raise NotFoundError()
            if preservation.status != "ready":
                raise ConflictError(error_messages.PRESERVATION_NOT_READY)
        nodes = list(
            db.scalars(
                select(ArtifactReleaseNode)
                .where(ArtifactReleaseNode.release_id == release.id)
                .order_by(ArtifactReleaseNode.ordinal)
            )
        )
        identity = uuid5(
            NAMESPACE, f"publication:{project_id}:{actor.id}:{data.request_id}"
        )
        return (
            release,
            build_projection(release, nodes, preservation, identity=identity),
            identity,
        )

    def preview(self, original, user, project_id, data):
        """副作用なしで公開内容と除外対象を要約する。"""
        with self.access(original, user, project_id, write=True) as (db, actor):
            _, projection, _ = self.sources(db, actor, project_id, data)
            return PublicationPreview.model_validate(projection.preview())

    def create(self, original, user, project_id, data):
        """確認した表現だけを原子的・冪等に保存し、公開はしない。"""
        for attempt in range(3):
            try:
                with self.access(original, user, project_id, write=True) as (db, actor):
                    previous = self.repository.by_request(
                        db, project_id, actor.id, data.request_id
                    )
                    if previous is not None:
                        self._readable(db, actor, project_id, previous)
                        if previous.request_digest != digest(
                            data.model_dump(mode="json")
                        ):
                            raise DuplicateResourceError()
                        return self._read(previous), False
                    release, projection, identity = self.sources(
                        db, actor, project_id, data
                    )
                    if (
                        data.expected_public_digest != projection.public_digest
                        or data.expected_source_manifest_digest
                        != projection.source_manifest_digest
                    ):
                        raise VersionConflictError(
                            {
                                "public_digest": projection.public_digest,
                                "source_manifest_digest": (
                                    projection.source_manifest_digest
                                ),
                            }
                        )
                    row = self.repository.create(
                        db,
                        dict(
                            id=identity,
                            project_id=project_id,
                            release_id=release.id,
                            preservation_id=data.preservation_id,
                            schema_version=1,
                            projection_version=data.projection_version,
                            title=projection.title,
                            source_kinds=release.source_kinds,
                            source_manifest=projection.source_manifest,
                            source_manifest_digest=projection.source_manifest_digest,
                            file_manifest=projection.files,
                            public_digest=projection.public_digest,
                            body_digest=release.content_digest,
                            byte_size=projection.byte_size,
                            node_count=len(projection.nodes),
                            captured_at=release.captured_at,
                            created_by=actor.id,
                            request_id=data.request_id,
                            request_digest=digest(data.model_dump(mode="json")),
                            sealed=False,
                        ),
                        projection.nodes,
                    )
                    AuditLogService().record(
                        db,
                        event_type="artifact_publication.prepared",
                        actor_user_id=actor.id,
                        project_id=project_id,
                        resource_type="artifact_publication",
                        metadata={"publication_id": str(row.id)},
                        commit=False,
                    )
                    result = self._read(row)
                return result, True
            except DBAPIError as exc:
                code = getattr(exc.orig, "pgcode", None)
                constraint = getattr(
                    getattr(exc.orig, "diag", None), "constraint_name", None
                )
                if code not in {"40001", "40P01"} and not (
                    code == "23505"
                    and constraint
                    in {"uq_artifact_publication_request", "artifact_publications_pkey"}
                ):
                    raise
                if attempt == 2:
                    raise ConflictError(
                        error_messages.ARTIFACT_RETRY_CONFLICT
                    ) from None
        raise AssertionError("unreachable")

    def _readable(self, db, actor, project_id, row):
        """保存後も全sourceと添付のreadを現在の権限で確認する。"""
        self.releases._sources(db, actor, project_id, row.source_kinds)
        if row.preservation_id is not None:
            self.releases._permission(db, actor, project_id, "document:read")

    def _get(self, db, actor, project_id, identity):
        """同一Project内だけで存在を解決する。"""
        row = self.repository.get(db, project_id, identity)
        if row is None:
            raise NotFoundError()
        self._readable(db, actor, project_id, row)
        return row

    @staticmethod
    def _read(row):
        """公開用metadataへ内部対応を混ぜない。"""
        return PublicationRead(
            state=row.publication_state,
            publication_version=row.publication_version,
            content=PublicationContentMetadata(
                id=row.id,
                title=row.title,
                schema_version=row.schema_version,
                projection_version=cast(PublicProjection, row.projection_version),
                public_digest=row.public_digest,
                node_count=row.node_count,
                byte_size=row.byte_size,
                captured_at=row.captured_at,
                files=row.file_manifest,
            ),
            internal_origin=PublicationInternalOrigin(
                release_id=row.release_id,
                preservation_id=row.preservation_id,
                source_manifest_digest=row.source_manifest_digest,
                body_digest=row.body_digest,
            ),
        )

    def metadata(self, original, user, project_id, identity):
        """内部の現在権限で準備版を読む。"""
        with self.access(original, user, project_id) as (db, actor):
            return self._read(self._get(db, actor, project_id, identity))

    def list(self, original, user, project_id, release_id, page, page_size):
        """全種類と添付のreadを一覧・COUNTにも適用する。"""
        with self.access(original, user, project_id) as (db, actor):
            auth = self.releases.authorization
            kinds = [
                k
                for k, prefix in SOURCE_PERMISSIONS.items()
                if auth.has_project_permission(
                    db,
                    user=actor,
                    project_id=project_id,
                    permission_code=f"{prefix}:read",
                )
            ]
            document_read = auth.has_project_permission(
                db, user=actor, project_id=project_id, permission_code="document:read"
            )
            query = self.repository.visible(project_id, kinds, document_read)
            if release_id is not None:
                query = query.where(ArtifactPublication.release_id == release_id)
            total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = db.scalars(
                query.order_by(
                    ArtifactPublication.created_at.desc(), ArtifactPublication.id.desc()
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            return PublicationList(
                items=[
                    PublicationSummary.model_validate(
                        {
                            **{
                                k: getattr(r, k)
                                for k in PublicationSummary.model_fields
                                if k not in {"state", "publication_version"}
                            },
                            "state": r.publication_state,
                            "publication_version": r.publication_version,
                        }
                    )
                    for r in rows
                ],
                total=total,
                page=page,
                page_size=page_size,
            )

    def nodes(self, original, user, project_id, identity: UUID, cursor, page_size):
        """本文をbyte上限で分割し、cursorを本人/Project/版へ束縛する。"""
        with self.access(original, user, project_id) as (db, actor):
            row = self._get(db, actor, project_id, identity)
            purpose = (
                f"artifact-publication:{actor.id}:{identity}:{row.projection_version}"
            )
            offset = decode_cursor(cursor, project_id, purpose, row.public_digest)
            if offset >= row.node_count:
                raise BadRequestError(error_messages.ARTIFACT_CURSOR_INVALID)
            result = PublicationNodePage(
                publication_id=identity,
                projection_version=cast(PublicProjection, row.projection_version),
                items=[],
                total=row.node_count,
                next_cursor=None,
            )
            for node in self.repository.nodes(db, identity, offset - 1, page_size):
                items = [*result.items, PublicationNodeRead.model_validate(node)]
                next_offset = node.ordinal + 1
                proposed = result.model_copy(
                    update={
                        "items": items,
                        "next_cursor": encode_cursor(
                            project_id, purpose, row.public_digest, next_offset
                        )
                        if next_offset < row.node_count
                        else None,
                    }
                )
                if len(proposed.model_dump_json().encode()) > MAX_PAGE_BYTES:
                    if not result.items:
                        raise ConflictError(error_messages.ARTIFACT_RETRY_CONFLICT)
                    break
                result = proposed
            return result
