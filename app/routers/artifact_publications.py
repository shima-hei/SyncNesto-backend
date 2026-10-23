"""内部専用の公開準備版。外部公開や配信のAPIを含まない。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.artifact_publication import (
    PublicationCreateRequest,
    PublicationList,
    PublicationNodePage,
    PublicationPreview,
    PublicationPreviewRequest,
    PublicationRead,
)
from app.services.artifact_publication import ArtifactPublicationService

router = APIRouter(
    prefix="/projects/{project_id}/artifact-publications",
    tags=["artifact-publications"],
)
Db = Annotated[Session, Depends(get_db)]
Manager = Annotated[
    User, Depends(require_project_permission("artifact_publication:manage"))
]
Reader = Annotated[
    User, Depends(require_project_permission("artifact_publication:read"))
]
service = ArtifactPublicationService()


@router.post("/preview", response_model=PublicationPreview)
def preview_publication(
    project_id: int, data: PublicationPreviewRequest, db: Db, user: Manager
):
    """準備する本文と添付構成を保存前に確認する。"""
    return service.preview(db, user, project_id, data)


@router.post("", response_model=PublicationRead, status_code=201)
def create_publication(
    project_id: int,
    data: PublicationCreateRequest,
    db: Db,
    user: Manager,
    response: Response,
):
    """未公開の準備版を保存する。同じ操作の再送は同じ結果を返す。"""
    result, created = service.create(db, user, project_id, data)
    response.status_code = 201 if created else 200
    return result


@router.get("", response_model=PublicationList)
def list_publications(
    project_id: int,
    db: Db,
    user: Reader,
    release_id: UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """現在の許可範囲にある準備版だけを一覧する。"""
    return service.list(db, user, project_id, release_id, page, page_size)


@router.get("/{publication_id}", response_model=PublicationRead)
def read_publication(project_id: int, publication_id: UUID, db: Db, user: Reader):
    """相手向けの内容metadataと社内対応を分離して取得する。"""
    return service.metadata(db, user, project_id, publication_id)


@router.get("/{publication_id}/nodes", response_model=PublicationNodePage)
def read_publication_nodes(
    project_id: int,
    publication_id: UUID,
    db: Db,
    user: Reader,
    cursor: str | None = Query(default=None, max_length=1000),
    page_size: int = Query(default=50, ge=1, le=100),
):
    """公開用本文を順序・byte上限・本人に束縛したcursorで取得する。"""
    return service.nodes(db, user, project_id, publication_id, cursor, page_size)
