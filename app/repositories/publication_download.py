"""公開添付から同Projectの採用済み保全実体だけを解決する。"""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select

from app.core.exceptions import NotFoundError, SharedFileUnavailableError
from app.models.artifact_preservation import (
    ArtifactPreservation,
    ArtifactPreservationObject,
)
from app.models.artifact_release import ArtifactRelease
from app.models.project import Project
from app.services.artifact_publication_projection import public_id
from app.services.artifact_release_snapshot import digest


@dataclass(frozen=True)
class DeliverySource:
    """公開DTOに出さない、不変な取得対応。"""

    object_id: UUID
    project_id: int
    tenant_id: int
    filename: str
    byte_size: int
    content_digest: str
    storage_key: str = field(repr=False)


class PublicationDownloadRepository:
    """認可済み公開版を起点にし、任意の保存先検索を提供しない。"""

    def resolve(self, db, publication, file_id: UUID) -> DeliverySource:
        """採用・ready・Project・両manifest・実体digestを照合する。"""
        visible = next(
            (f for f in publication["file_manifest"] if f["id"] == str(file_id)),
            None,
        )
        if visible is None:
            raise NotFoundError()
        preservation = db.get(ArtifactPreservation, publication["preservation_id"])
        release = db.get(ArtifactRelease, publication["release_id"])
        if (
            preservation is None
            or release is None
            or preservation.status != "ready"
            or preservation.release_id != release.id
            or release.project_id != publication["project_id"]
            or preservation.manifest is None
        ):
            raise NotFoundError()
        origin = publication["source_manifest"]
        if (
            digest(origin) != publication["source_manifest_digest"]
            or origin.get("release_id") != str(release.id)
            or origin.get("body_digest") != release.content_digest
            or origin.get("preservation_id") != str(preservation.id)
            or origin.get("preservation_digest") != preservation.manifest_digest
            or digest(preservation.manifest) != preservation.manifest_digest
        ):
            raise SharedFileUnavailableError()
        for slot, source in enumerate(preservation.manifest["files"]):
            if public_id("file", f"{preservation.id}:{slot}") != str(file_id):
                continue
            obj = db.scalar(
                select(ArtifactPreservationObject).where(
                    ArtifactPreservationObject.preservation_id == preservation.id,
                    ArtifactPreservationObject.slot == slot,
                    ArtifactPreservationObject.selected.is_(True),
                    ArtifactPreservationObject.state == "verified",
                )
            )
            if obj is None:
                raise NotFoundError()
            fields = ("filename", "byte_size", "content_type", "content_digest")
            if (
                any(visible[k] != source[k] for k in fields)
                or obj.content_digest != source["content_digest"]
                or obj.byte_size != source["byte_size"]
            ):
                raise SharedFileUnavailableError()
            tenant_id = db.scalar(
                select(Project.tenant_id).where(Project.id == release.project_id)
            )
            return DeliverySource(
                object_id=obj.id,
                project_id=release.project_id,
                tenant_id=tenant_id,
                filename=visible["filename"],
                byte_size=visible["byte_size"],
                content_digest=visible["content_digest"],
                storage_key=obj.storage_key,
            )
        raise NotFoundError()
