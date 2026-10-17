"""内部向け添付保全の準備・進行・確認・回収。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.routers.artifact_releases import Creator, Db, Reader
from app.schemas.artifact_preservation import (
    PreservationCleanupRequest,
    PreservationCreateRequest,
    PreservationDownload,
    PreservationList,
    PreservationPreview,
    PreservationPreviewRequest,
    PreservationRead,
)
from app.services.artifact_preservation import ArtifactPreservationService

router = APIRouter(
    prefix="/projects/{project_id}/artifact-releases/{release_id}/attachment-preservations",
    tags=["artifact-preservations"],
)
service = ArtifactPreservationService()


@router.post("/preview", response_model=PreservationPreview)
def preview_preservation(
    project_id: int,
    release_id: UUID,
    data: PreservationPreviewRequest,
    db: Db,
    user: Creator,
):
    """固定本文に含める添付の選択構成を確認する。"""
    return service.preview(db, user, project_id, release_id, data)


@router.post("", response_model=PreservationRead, status_code=201)
def reserve_preservation(
    project_id: int,
    release_id: UUID,
    data: PreservationCreateRequest,
    response: Response,
    db: Db,
    user: Creator,
):
    """選択構成を冪等に予約する。コピーは明示操作で進める。"""
    result, created = service.create(db, user, project_id, release_id, data)
    response.status_code = 201 if created else 200
    return result


@router.get("", response_model=PreservationList)
def list_preservations(
    project_id: int,
    release_id: UUID,
    db: Db,
    user: Reader,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """同じ認可範囲の一覧と件数を返す。"""
    return service.list(db, user, project_id, release_id, page, page_size)


@router.get("/{preservation_id}", response_model=PreservationRead)
def read_preservation(
    project_id: int, release_id: UUID, preservation_id: UUID, db: Db, user: Reader
):
    """準備・完成・失敗を区別して表示する。"""
    return service.get(db, user, project_id, release_id, preservation_id)


@router.post("/{preservation_id}/advance", response_model=PreservationRead)
def advance_preservation(
    project_id: int, release_id: UUID, preservation_id: UUID, db: Db, user: Creator
):
    """最大1ファイルを保全し、途中から再開できるようにする。"""
    return service.advance(db, user, project_id, release_id, preservation_id)


@router.post("/{preservation_id}/cleanup", response_model=PreservationRead)
def cleanup_preservation(
    project_id: int,
    release_id: UUID,
    preservation_id: UUID,
    data: PreservationCleanupRequest,
    db: Db,
    user: Creator,
):
    """非採用試行または明示取消した準備のコピーだけを回収する。"""
    return service.cleanup(
        db, user, project_id, release_id, preservation_id, data.cancel_preparation
    )


@router.get(
    "/{preservation_id}/files/{file_id}/download", response_model=PreservationDownload
)
def download_preserved_attachment(
    project_id: int,
    release_id: UUID,
    preservation_id: UUID,
    file_id: UUID,
    response: Response,
    db: Db,
    user: Reader,
):
    """完成した保全実体にだけ短期URLを発行する。"""
    response.headers["Cache-Control"] = "no-store"
    return service.download(db, user, project_id, release_id, preservation_id, file_id)
