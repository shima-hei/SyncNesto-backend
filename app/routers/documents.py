"""Project文書の本文、版履歴、添付と関連付けAPI。"""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.document import (
    DocumentAttachmentRead,
    DocumentCreate,
    DocumentDownload,
    DocumentLinkCreate,
    DocumentLinkRead,
    DocumentLinkTarget,
    DocumentList,
    DocumentRead,
    DocumentRevisionRead,
    DocumentRevisionSummary,
    DocumentTargetType,
    DocumentUpdate,
)
from app.schemas.file_upload import (
    FileUploadComplete,
    FileUploadPlan,
    FileUploadRequest,
)
from app.services.document import DocumentService
from app.services.document_attachment import (
    MAX_DOCUMENT_BYTES,
    DocumentAttachmentService,
)

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])
service = DocumentService()
attachments = DocumentAttachmentService()


@router.get("", response_model=DocumentList)
def list_documents(
    project_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, max_length=200),
    _: User = Depends(require_project_permission("document:read")),
    db: Session = Depends(get_db),
) -> DocumentList:
    """タイトル・本文検索とページングした文書一覧。"""
    return service.list(db, project_id, page, page_size, q)


@router.post("", response_model=DocumentRead, status_code=201)
def create_document(
    project_id: int,
    data: DocumentCreate,
    user: User = Depends(require_project_permission("document:create")),
    db: Session = Depends(get_db),
):
    """Markdown本文と版1を作成する。"""
    return service.create(db, project_id, data, user.id)


@router.get("/link-candidates", response_model=list[DocumentLinkTarget])
def document_link_candidates(
    project_id: int,
    target_type: DocumentTargetType,
    q: str | None = Query(None, max_length=200),
    user: User = Depends(require_project_permission("document:read")),
    db: Session = Depends(get_db),
):
    """参照先の権限も確認した関連候補。"""
    return service.candidates(db, project_id, target_type, user, q)


@router.get("/{document_id}", response_model=DocumentRead)
def read_document(
    project_id: int,
    document_id: int,
    _: User = Depends(require_project_permission("document:read")),
    db: Session = Depends(get_db),
):
    """現在の文書を取得する。"""
    return service.get(db, project_id, document_id)


@router.patch("/{document_id}", response_model=DocumentRead)
def update_document(
    project_id: int,
    document_id: int,
    data: DocumentUpdate,
    user: User = Depends(require_project_permission("document:update")),
    db: Session = Depends(get_db),
):
    """楽観ロック付きで本文を更新し、版を追加する。"""
    return service.update(db, project_id, document_id, data, user.id)


@router.delete("/{document_id}", status_code=204)
def delete_document(
    project_id: int,
    document_id: int,
    version: int = Query(ge=1),
    user: User = Depends(require_project_permission("document:delete")),
    db: Session = Depends(get_db),
) -> Response:
    """本文と子リソースの取得経路を閉じる。"""
    service.delete(db, project_id, document_id, version, user.id)
    return Response(status_code=204)


@router.get("/{document_id}/revisions", response_model=list[DocumentRevisionSummary])
def list_document_revisions(
    project_id: int,
    document_id: int,
    _: User = Depends(require_project_permission("document:read")),
    db: Session = Depends(get_db),
):
    """最新100版までの概要。本文は指定版APIで取得する。"""
    return service.revisions(db, project_id, document_id)


@router.get("/{document_id}/revisions/{number}", response_model=DocumentRevisionRead)
def read_document_revision(
    project_id: int,
    document_id: int,
    number: int,
    _: User = Depends(require_project_permission("document:read")),
    db: Session = Depends(get_db),
):
    """過去の指定版を取得する。"""
    return service.revision(db, project_id, document_id, number)


@router.get("/{document_id}/attachments", response_model=list[DocumentAttachmentRead])
def list_document_attachments(
    project_id: int,
    document_id: int,
    _: User = Depends(require_project_permission("document:read")),
    db: Session = Depends(get_db),
):
    """署名URLを含めず添付一覧を取得する。"""
    return service.attachments(db, project_id, document_id)


@router.post("/{document_id}/attachments/upload-plan", response_model=FileUploadPlan)
def plan_document_attachment(
    project_id: int,
    document_id: int,
    data: FileUploadRequest,
    user: User = Depends(require_project_permission("document:update")),
    db: Session = Depends(get_db),
):
    """直接送信を本人・文書・期限に結び付ける。"""
    return attachments.plan(db, project_id, document_id, data, user.id)


@router.post(
    "/{document_id}/attachments/upload-complete",
    response_model=DocumentAttachmentRead,
    status_code=201,
)
def complete_document_attachment(
    project_id: int,
    document_id: int,
    data: FileUploadComplete,
    user: User = Depends(require_project_permission("document:update")),
    db: Session = Depends(get_db),
):
    """一時ファイルを検証して確定する。"""
    return attachments.complete(db, project_id, document_id, data.upload_token, user.id)


@router.post(
    "/{document_id}/attachments", response_model=DocumentAttachmentRead, status_code=201
)
async def upload_document_attachment(
    project_id: int,
    document_id: int,
    file: UploadFile = File(...),
    user: User = Depends(require_project_permission("document:update")),
    db: Session = Depends(get_db),
):
    """サーバー送信モードの添付を上限付きで受け取る。"""
    return attachments.upload(
        db,
        project_id,
        document_id,
        file.filename or "",
        file.content_type or "",
        await file.read(MAX_DOCUMENT_BYTES + 1),
        user.id,
    )


@router.get(
    "/{document_id}/attachments/{attachment_id}/download",
    response_model=DocumentDownload,
)
def download_document_attachment(
    project_id: int,
    document_id: int,
    attachment_id: UUID,
    _: User = Depends(require_project_permission("document:read")),
    db: Session = Depends(get_db),
) -> DocumentDownload:
    """非公開添付の短期ダウンロードURL。"""
    return DocumentDownload(
        url=attachments.download(db, project_id, document_id, attachment_id)
    )


@router.delete("/{document_id}/attachments/{attachment_id}", status_code=204)
def delete_document_attachment(
    project_id: int,
    document_id: int,
    attachment_id: UUID,
    user: User = Depends(require_project_permission("document:update")),
    db: Session = Depends(get_db),
) -> Response:
    """添付の以後の取得を拒否する。"""
    attachments.delete(db, project_id, document_id, attachment_id, user.id)
    return Response(status_code=204)


@router.get("/{document_id}/links", response_model=list[DocumentLinkRead])
def list_document_links(
    project_id: int,
    document_id: int,
    user: User = Depends(require_project_permission("document:read")),
    db: Session = Depends(get_db),
):
    """関連先の閲覧権限を考慮して参照を返す。"""
    return service.links(db, project_id, document_id, user)


@router.post(
    "/{document_id}/links", response_model=list[DocumentLinkRead], status_code=201
)
def create_document_link(
    project_id: int,
    document_id: int,
    data: DocumentLinkCreate,
    user: User = Depends(require_project_permission("document:update")),
    db: Session = Depends(get_db),
):
    """同一Project内の資源へ関連付ける。"""
    return service.add_link(db, project_id, document_id, data, user)


@router.delete("/{document_id}/links/{link_id}", status_code=204)
def delete_document_link(
    project_id: int,
    document_id: int,
    link_id: int,
    user: User = Depends(require_project_permission("document:update")),
    db: Session = Depends(get_db),
) -> Response:
    """文書の関連を解除する。"""
    service.delete_link(db, project_id, document_id, link_id, user.id)
    return Response(status_code=204)
