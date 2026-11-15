"""Project内の固定版を作成し、metadataと本文を分割して取得する。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.artifact_release import (
    ArtifactCreateRequest,
    ArtifactNodePage,
    ArtifactPreview,
    ArtifactPreviewRequest,
    ArtifactReleaseList,
    ArtifactReleaseMetadata,
    SourceKind,
)
from app.services.artifact_release import ArtifactReleaseService

router = APIRouter(
    prefix="/projects/{project_id}/artifact-releases", tags=["artifact-releases"]
)
Db = Annotated[Session, Depends(get_db)]
Creator = Annotated[
    User, Depends(require_project_permission("artifact_release:create"))
]
Reader = Annotated[User, Depends(require_project_permission("artifact_release:read"))]
service = ArtifactReleaseService()


@router.post("/preview", response_model=ArtifactPreview)
def preview_release(
    project_id: int, data: ArtifactPreviewRequest, db: Db, user: Creator
):
    """保存前に構成・元版・内容hashを確認する。"""
    return service.preview(db, user, project_id, data)


@router.post("", response_model=ArtifactReleaseMetadata, status_code=201)
def create_release(
    project_id: int,
    data: ArtifactCreateRequest,
    db: Db,
    user: Creator,
    response: Response,
):
    """確認済みの対象を固定し、同じ操作の再送には元の結果を返す。"""
    result, created = service.create(db, user, project_id, data)
    response.status_code = 201 if created else 200
    return result


@router.get("", response_model=ArtifactReleaseList)
def list_releases(
    project_id: int,
    db: Db,
    user: Reader,
    source_kind: SourceKind | None = None,
    source_id: int | None = Query(default=None, gt=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """現在の許可範囲で固定版を一覧する。"""
    return service.list(db, user, project_id, source_kind, source_id, page, page_size)


@router.get("/{release_id}", response_model=ArtifactReleaseMetadata)
def read_release(project_id: int, release_id: UUID, db: Db, user: Reader):
    """本文を再参照せず固定版のmetadataを取得する。"""
    return service.metadata(db, user, project_id, release_id)


@router.get("/{release_id}/nodes", response_model=ArtifactNodePage)
def read_release_nodes(
    project_id: int,
    release_id: UUID,
    db: Db,
    user: Reader,
    cursor: str | None = Query(default=None, max_length=1000),
    page_size: int = Query(default=50, ge=1, le=100),
):
    """固定した本文をbyte上限と署名cursorで分割取得する。"""
    return service.nodes(db, user, project_id, release_id, cursor, page_size)
