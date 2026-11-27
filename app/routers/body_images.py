"""本文画像の送信と、現在の権限に基づく配信。"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Response, UploadFile

from app.core.config import settings
from app.core.exceptions import BadRequestError, NotFoundError
from app.routers.comment_images import Actor, Db
from app.schemas.comment_image import CommentImageRead
from app.schemas.file_upload import (
    FileUploadComplete,
    FileUploadPlan,
    FileUploadRequest,
)
from app.services.authorization import AuthorizationService
from app.services.body_image import BodyImageKind, BodyImageService
from app.services.comment_image import DRAFT_RETENTION, MAX_COMMENT_IMAGE_BYTES
from app.services.shared_publication import SharedPublicationService

router = APIRouter(tags=["body-images"])
shared_router = APIRouter(tags=["body-images"])
service = BodyImageService()
shared_service = SharedPublicationService()
BASE = "/projects/{project_id}/body-images/{kind}"
PERMISSIONS = {
    "document": ("document:read", "document:create", "document:update"),
    "requirement": ("requirement:read", "requirement:create", "requirement:update"),
    "specification": ("document:read", "document:create", "document:update"),
    "test_design": ("test_plan:read", "test_plan:create", "test_plan:update"),
    "task": ("task:read", "task:create", "task:update"),
}


def allow(db: Db, project_id: int, user: Actor, kind: str, *, write=False) -> None:
    """既存の本文操作権限に従い、画像用途を分離する。"""
    read, *edit = PERMISSIONS[kind]
    if not any(
        AuthorizationService().has_project_permission(
            db, user=user, project_id=project_id, permission_code=permission
        )
        for permission in (edit if write else [read])
    ):
        raise NotFoundError()


@router.post(BASE + "/upload-plan", response_model=FileUploadPlan)
def plan_body_image(
    project_id: int, kind: BodyImageKind, data: FileUploadRequest, db: Db, user: Actor
):
    """既存の署名付き送信手順を本文画像へ適用する。"""
    allow(db, project_id, user, kind, write=True)
    return service.plan(db, project_id, f"body_{kind}", data, user.id)


@router.post(
    BASE + "/upload-complete", response_model=CommentImageRead, status_code=201
)
def complete_body_image(
    project_id: int, kind: BodyImageKind, data: FileUploadComplete, db: Db, user: Actor
):
    """アップロード元と画像の実体を確認する。"""
    allow(db, project_id, user, kind, write=True)
    return service.complete(db, project_id, f"body_{kind}", data.upload_token, user.id)


@router.post(BASE + "/upload", response_model=CommentImageRead, status_code=201)
def upload_body_image(
    project_id: int,
    kind: BodyImageKind,
    db: Db,
    user: Actor,
    file: Annotated[UploadFile, File()],
):
    """直接送信を使わない環境用の経路。"""
    allow(db, project_id, user, kind, write=True)
    if settings.file_upload_mode != "server":
        raise BadRequestError("アップロードをやり直してください。")
    return service.upload(
        db,
        project_id,
        f"body_{kind}",
        file.filename or "",
        file.content_type or "",
        file.file.read(MAX_COMMENT_IMAGE_BYTES + 1),
        user.id,
    )


@router.delete(BASE + "/{image_id}", status_code=204)
def discard_body_image(
    project_id: int, kind: BodyImageKind, image_id: UUID, db: Db, user: Actor
):
    """保存に使われていない本人の下書きを取り消す。"""
    allow(db, project_id, user, kind, write=True)
    row = service.get(db, project_id, image_id)
    if row.kind != f"body_{kind}":
        raise NotFoundError()
    service.discard_draft(db, project_id, image_id, user.id)


@router.get("/body-images/{image_id}", response_class=Response)
def view_body_image(image_id: UUID, db: Db, user: Actor):
    """本文用の画像だけを現在の案件・用途の権限で配信する。"""
    row = service.stored_image(db, image_id)
    allow(
        db,
        row.project_id,
        user,
        row.kind.removeprefix("body_"),
        write=row.retained_at is None,
    )
    if row.retained_at is None and (
        row.uploaded_by != user.id
        or row.created_at < datetime.now(UTC) - DRAFT_RETENTION
    ):
        raise NotFoundError()
    return body_image_response(service.content(db, row), row.content_type)


@shared_router.get(
    "/shared/publications/{publication_id}/nodes/{node_id}/body-images/{image_id}",
    response_class=Response,
)
def shared_publication_body_image(
    publication_id: UUID, node_id: UUID, image_id: UUID, db: Db, user: Actor
):
    """当該固定版の本文で公開された画像のみ配信する。"""
    return body_image_response(
        *shared_service.body_image(db, user, publication_id, node_id, image_id)
    )


def body_image_response(content: bytes, content_type: str) -> Response:
    """配信URLを再利用した場合も、毎回認可する。"""
    return Response(
        content,
        media_type=content_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )
