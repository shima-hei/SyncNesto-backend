"""コメントの権限で非公開画像を送信・閲覧する。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.db.session import get_db
from app.models.comment_image import CommentImage
from app.models.specification import SpecificationComment
from app.models.test_design import TestDesignComment
from app.models.user import User
from app.routers import requirements_shared
from app.routers.tasks_shared import task_comment_service, task_service
from app.schemas.comment_image import CommentImageKind, CommentImageRead
from app.schemas.file_upload import (
    FileUploadComplete,
    FileUploadPlan,
    FileUploadRequest,
)
from app.services.authorization import AuthorizationService
from app.services.comment_image import MAX_COMMENT_IMAGE_BYTES, CommentImageService
from app.services.specification import SpecificationService
from app.services.test_design import TestDesignService

router = APIRouter(
    prefix="/projects/{project_id}/comment-images", tags=["comment-images"]
)
service = CommentImageService()
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[User, Depends(get_current_user)]
PERMISSIONS = {
    "task": ("task:read", "task:comment"),
    "requirement": ("requirement:read", "requirement:comment"),
    "specification": ("document:read", "document:update"),
    "test_design": ("test_plan:read", "test_plan:comment"),
}


def require_permission(
    db: Session, project_id: int, user: User, kind: str, write: bool = False
) -> None:
    if not AuthorizationService().has_project_permission(
        db,
        user=user,
        project_id=project_id,
        permission_code=PERMISSIONS[kind][int(write)],
    ):
        raise ForbiddenError()


@router.post("/{kind}/upload-plan", response_model=FileUploadPlan)
def plan_comment_image(
    project_id: int,
    kind: CommentImageKind,
    data: FileUploadRequest,
    db: Db,
    user: Actor,
):
    """許可のある案件のコメント種別にアップロードを予約する。"""
    require_permission(db, project_id, user, kind, write=True)
    return service.plan(db, project_id, kind, data, user.id)


@router.post(
    "/{kind}/upload-complete", response_model=CommentImageRead, status_code=201
)
def complete_comment_image(
    project_id: int,
    kind: CommentImageKind,
    data: FileUploadComplete,
    db: Db,
    user: Actor,
):
    """署名の本人と用途を確認し投稿前の画像として登録する。"""
    require_permission(db, project_id, user, kind, write=True)
    return service.complete(db, project_id, kind, data.upload_token, user.id)


@router.post("/{kind}/upload", response_model=CommentImageRead, status_code=201)
def upload_comment_image(
    project_id: int,
    kind: CommentImageKind,
    db: Db,
    user: Actor,
    file: Annotated[UploadFile, File()],
):
    """直接送信を使わない環境のアップロード。"""
    require_permission(db, project_id, user, kind, write=True)
    if settings.file_upload_mode != "server":
        raise BadRequestError("アップロードをやり直してください。")
    return service.upload(
        db,
        project_id,
        kind,
        file.filename or "",
        file.content_type or "",
        file.file.read(MAX_COMMENT_IMAGE_BYTES + 1),
        user.id,
    )


@router.delete("/{image_id}", status_code=204)
def discard_comment_image(project_id: int, image_id: UUID, db: Db, user: Actor):
    """投稿に使用していない自分の画像を取り消す。"""
    row = service.get(db, project_id, image_id)
    require_permission(db, project_id, user, row.kind, write=True)
    service.discard_draft(db, project_id, image_id, user.id)


def authorize_owner(db: Session, project_id: int, row: CommentImage) -> None:
    """各機能が使っている対象の生存・所属検証を適用する。"""
    if row.task_comment_id is not None:
        task = task_service.get_task(
            db, task_comment_service.get_comment(db, row.task_comment_id).task_id
        )
        if task.project_id != project_id:
            raise NotFoundError()
    elif row.requirement_comment_id is not None:
        requirements_shared.target_comment_service.get_comment(
            db, project_id=project_id, comment_id=row.requirement_comment_id
        )
    elif row.specification_comment_id is not None:
        specification_comment = db.get(
            SpecificationComment, row.specification_comment_id
        )
        if specification_comment is None:
            raise NotFoundError()
        SpecificationService().get(
            db, project_id, specification_comment.specification_id
        )
    elif row.test_comment_id is not None:
        test_comment = db.get(TestDesignComment, row.test_comment_id)
        if test_comment is None or test_comment.deleted_at is not None:
            raise NotFoundError()
        TestDesignService().get(db, project_id, test_comment.design_id)
    else:
        raise NotFoundError()


@router.get("/{image_id}", response_class=Response)
def view_comment_image(project_id: int, image_id: UUID, db: Db, user: Actor):
    """画像ごとに認可し、ブラウザへ共有可能な保存先URLを発行しない。"""
    row = service.get(db, project_id, image_id)
    require_permission(db, project_id, user, row.kind)
    authorize_owner(db, project_id, row)
    return Response(
        service.content(db, row),
        media_type=row.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
