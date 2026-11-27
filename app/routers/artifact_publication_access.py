"""内部の公開操作・参加管理と、本人限定sharedの取得経路。"""

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_project_permission
from app.core.tenant import require_project_management
from app.db.session import get_db
from app.models.user import User
from app.schemas.artifact_publication_access import (
    GuestParticipationDecisionList,
    GuestParticipationExpiry,
    GuestParticipationList,
    GuestPublicationGrantList,
    PublicationEventList,
    PublicationGrantList,
    PublicationOperationResult,
    PublicationPublish,
    PublicationRevoke,
    SharedPublicationFiles,
    SharedPublicationLinkTitle,
    SharedPublicationList,
    SharedPublicationNodePage,
    SharedPublicationRead,
)
from app.schemas.artifact_release import SourceKind
from app.schemas.file_upload import (
    FileUploadComplete,
    FileUploadPlan,
    FileUploadRequest,
)
from app.schemas.publication_download import (
    SharedFileDownload,
    SharedFileDownloadRequest,
)
from app.schemas.shared_publication_comment import (
    SharedCommentFileRead,
    SharedCommentMentionCandidate,
    SharedPublicationCommentCreate,
    SharedPublicationCommentList,
    SharedPublicationCommentRead,
)
from app.services.artifact_publication_access import ArtifactPublicationAccessService
from app.services.publication_download import PublicationDownloadService
from app.services.shared_publication import SharedPublicationService
from app.services.shared_publication_comment import SharedPublicationCommentService

router = APIRouter(prefix="/projects/{project_id}", tags=["publication-access"])
shared_router = APIRouter(prefix="/shared/publications", tags=["shared-publications"])
Db = Annotated[Session, Depends(get_db)]
Publisher = Annotated[
    User, Depends(require_project_permission("artifact_publication:publish"))
]
Reader = Annotated[
    User, Depends(require_project_permission("artifact_publication:read"))
]
ParticipantReader = Annotated[
    User, Depends(require_project_management("project:invite_member"))
]
ParticipantManager = Annotated[
    User, Depends(require_project_management("project:remove_member"))
]
SharedReader = Annotated[User, Depends(get_current_user)]
service = ArtifactPublicationAccessService()
shared_service = SharedPublicationService()
download_service = PublicationDownloadService()
comments = SharedPublicationCommentService()
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=1000)]
CommentUpload = Annotated[UploadFile, File()]
UploadNonce = Annotated[UUID, Form()]


def _file_response(content, source, inline):
    disposition = "inline" if inline else "attachment"
    filename = quote(source.filename, safe="")
    return Response(
        content,
        media_type=source.content_type if inline else "application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/artifact-publications/recipient-candidates", response_model=GuestParticipationList
)
def publication_recipient_candidates(
    project_id: int,
    db: Db,
    user: Publisher,
    q: str = Query(default="", max_length=200),
    page: Page = 1,
    page_size: PageSize = 20,
):
    """全Identityの検索ではなく、有効な外部参加資格だけを候補にする。"""
    return service.recipients(db, user, project_id, q, page, page_size)


@router.post(
    "/artifact-publications/{publication_id}/publish",
    response_model=PublicationOperationResult,
)
def publish_artifact_publication(
    project_id: int,
    publication_id: UUID,
    data: PublicationPublish,
    db: Db,
    user: Publisher,
):
    """全文と相手を確認した固定版を初めて公開する。"""
    return service.publish(db, user, project_id, publication_id, data)


@router.post(
    "/artifact-publications/{publication_id}/withdraw",
    response_model=PublicationOperationResult,
)
def withdraw_artifact_publication(
    project_id: int,
    publication_id: UUID,
    data: PublicationRevoke,
    db: Db,
    user: Publisher,
):
    """公開全体を停止し、同じ版を再公開しない。"""
    return service.withdraw(db, user, project_id, publication_id, data)


@router.get(
    "/artifact-publications/{publication_id}/grants",
    response_model=PublicationGrantList,
)
def publication_grants(
    project_id: int,
    publication_id: UUID,
    db: Db,
    user: Reader,
    page: Page = 1,
    page_size: PageSize = 20,
):
    """全構成を読める内部本人に許可履歴を返す。"""
    return service.grants(db, user, project_id, publication_id, page, page_size)


@router.post(
    "/artifact-publications/{publication_id}/grants",
    response_model=PublicationOperationResult,
)
def add_publication_grants(
    project_id: int,
    publication_id: UUID,
    data: PublicationPublish,
    db: Db,
    user: Publisher,
):
    """公開済み固定版へ本人別許可を明示的に追加する。"""
    return service.publish(db, user, project_id, publication_id, data, adding=True)


@router.post(
    "/artifact-publications/{publication_id}/grants/{grant_id}/revoke",
    response_model=PublicationOperationResult,
)
def revoke_publication_grant(
    project_id: int,
    publication_id: UUID,
    grant_id: UUID,
    data: PublicationRevoke,
    db: Db,
    user: Publisher,
):
    """他本人を変えず、指定許可だけを失効する。"""
    return service.revoke_grant(db, user, project_id, publication_id, grant_id, data)


@router.get(
    "/artifact-publications/{publication_id}/events",
    response_model=PublicationEventList,
)
def publication_events(
    project_id: int,
    publication_id: UUID,
    db: Db,
    user: Reader,
    page: Page = 1,
    page_size: PageSize = 20,
):
    """公開と失効の業務証跡を返す。"""
    return service.events(db, user, project_id, publication_id, page, page_size)


@router.get("/guest-participations", response_model=GuestParticipationList)
def project_guest_participations(
    project_id: int,
    db: Db,
    user: ParticipantReader,
    page: Page = 1,
    page_size: PageSize = 20,
):
    """内部管理者に参加世代・状態だけを返す。"""
    return service.participations(db, user, project_id, page, page_size)


@router.post(
    "/guest-participations/{participation_id}/revoke",
    response_model=PublicationOperationResult,
)
def revoke_project_guest_participation(
    project_id: int,
    participation_id: UUID,
    data: PublicationRevoke,
    db: Db,
    user: ParticipantManager,
):
    """参加世代と、その世代の全公開許可を失効する。"""
    return service.revoke_participation(db, user, project_id, participation_id, data)


@router.put(
    "/guest-participations/{participation_id}/expiry",
    response_model=PublicationOperationResult,
)
def update_guest_participation_expiry(
    project_id: int,
    participation_id: UUID,
    data: GuestParticipationExpiry,
    db: Db,
    user: ParticipantReader,
):
    """確認した版の参加期限を変更する。"""
    return service.change_participation_expiry(
        db, user, project_id, participation_id, data
    )


@router.get(
    "/guest-participations/{participation_id}/grants",
    response_model=GuestPublicationGrantList,
)
def list_guest_participation_grants(
    project_id: int,
    participation_id: UUID,
    db: Db,
    user: ParticipantReader,
    page: Page = 1,
    page_size: PageSize = 20,
):
    """現在読める固定版の共有許可を参加者別に確認する。"""
    return service.participant_grants(
        db, user, project_id, participation_id, page, page_size
    )


@router.get(
    "/guest-participations/{participation_id}/decisions",
    response_model=GuestParticipationDecisionList,
)
def list_guest_participation_decisions(
    project_id: int,
    participation_id: UUID,
    db: Db,
    user: ParticipantReader,
    page: Page = 1,
    page_size: PageSize = 20,
):
    """選択した参加世代に対する現在閲覧できる依頼と回答を確認する。"""
    return service.participant_decisions(
        db, user, project_id, participation_id, page, page_size
    )


@shared_router.get("", response_model=SharedPublicationList)
def shared_publications(
    db: Db,
    user: SharedReader,
    q: str = Query(default="", max_length=200),
    kind: SourceKind | None = None,
    cursor: Cursor = None,
    page_size: PageSize = 20,
    participation_id: UUID | None = None,
):
    """認証済み本人へ現在許可された集合だけを返す。"""
    return shared_service.listing(
        db, user, q, kind, cursor, page_size, participation_id
    )


@shared_router.get("/link-titles", response_model=list[SharedPublicationLinkTitle])
def shared_publication_link_titles(
    db: Db,
    user: SharedReader,
    ids: Annotated[list[UUID], Query(min_length=1, max_length=50)],
):
    return shared_service.link_titles(db, user, ids)


@shared_router.get("/{publication_id}", response_model=SharedPublicationRead)
def shared_publication(publication_id: UUID, db: Db, user: SharedReader):
    """内部originや他人の許可を含まない固定内容を返す。"""
    return shared_service.metadata(db, user, publication_id)


@shared_router.get("/{publication_id}/nodes", response_model=SharedPublicationNodePage)
def shared_publication_nodes(
    publication_id: UUID,
    db: Db,
    user: SharedReader,
    cursor: Cursor = None,
    page_size: PageSize = 50,
):
    """現在の本人・sessionに結び付いた本文ページを返す。"""
    return shared_service.nodes(db, user, publication_id, cursor, page_size)


@shared_router.get("/{publication_id}/files", response_model=SharedPublicationFiles)
def shared_publication_files(publication_id: UUID, db: Db, user: SharedReader):
    """公開添付の一覧だけを返す。新しいDL経路は含めない。"""
    return shared_service.files(db, user, publication_id)


@shared_router.post(
    "/{publication_id}/files/{file_id}/download", response_model=SharedFileDownload
)
def download_shared_publication_file(
    publication_id: UUID,
    file_id: UUID,
    data: SharedFileDownloadRequest,
    db: Db,
    user: SharedReader,
):
    """現在許可された保全添付に限り、配信専用の短期URLを発行する。"""
    return download_service.download(db, user, publication_id, file_id, data)


@shared_router.get(
    "/{publication_id}/files/{file_id}/content",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
def shared_publication_file_content(
    publication_id: UUID,
    file_id: UUID,
    db: Db,
    user: SharedReader,
    revision: Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")],
    inline: bool = False,
):
    """毎回認可し、採用済み固定ファイルの内容だけを返す。"""
    content, source = download_service.content(
        db, user, publication_id, file_id, revision, inline=inline
    )
    return _file_response(content, source, inline)


@shared_router.get(
    "/{publication_id}/comments", response_model=SharedPublicationCommentList
)
def shared_publication_comments(
    publication_id: UUID,
    db: Db,
    user: SharedReader,
    parent_id: UUID | None = None,
    page: Page = 1,
    page_size: PageSize = 20,
    thread_id: UUID | None = None,
):
    """有効な閲覧許可で固定版の共有コメントを読む。"""
    return comments.listing(
        db, user, publication_id, parent_id, page, page_size, thread_id=thread_id
    )


@shared_router.get(
    "/{publication_id}/comments/mention-candidates",
    response_model=list[SharedCommentMentionCandidate],
)
def shared_comment_mention_candidates(
    publication_id: UUID,
    db: Db,
    user: SharedReader,
    q: Annotated[str, Query(max_length=255)] = "",
):
    return comments.mention_candidates(db, user, publication_id, q)


@router.get(
    "/artifact-publications/{publication_id}/comments/mention-candidates",
    response_model=list[SharedCommentMentionCandidate],
)
def publication_comment_mention_candidates(
    project_id: int,
    publication_id: UUID,
    db: Db,
    user: Reader,
    q: Annotated[str, Query(max_length=255)] = "",
):
    return comments.mention_candidates(db, user, publication_id, q, project_id)


@shared_router.post(
    "/{publication_id}/comments",
    response_model=SharedPublicationCommentRead,
    status_code=201,
)
def add_shared_publication_comment(
    publication_id: UUID,
    data: SharedPublicationCommentCreate,
    db: Db,
    user: SharedReader,
):
    """現在の投稿許可がある本人の投稿または返信を保存する。"""
    return comments.create(db, user, publication_id, data)


@shared_router.post(
    "/{publication_id}/comments/files/plan", response_model=FileUploadPlan
)
def plan_shared_comment_file(
    publication_id: UUID, data: FileUploadRequest, db: Db, user: SharedReader
):
    return comments.files.plan(db, user, publication_id, data)


@shared_router.post(
    "/{publication_id}/comments/files/complete",
    response_model=SharedCommentFileRead,
    status_code=201,
)
def complete_shared_comment_file(
    publication_id: UUID, data: FileUploadComplete, db: Db, user: SharedReader
):
    return comments.files.complete(db, user, publication_id, data.upload_token)


@shared_router.post(
    "/{publication_id}/comments/files",
    response_model=SharedCommentFileRead,
    status_code=201,
)
def upload_shared_comment_file(
    publication_id: UUID,
    db: Db,
    user: SharedReader,
    file: CommentUpload,
    request_id: UploadNonce,
):
    return comments.files.upload(db, user, publication_id, file, request_id)


@shared_router.delete("/{publication_id}/comments/files/{file_id}", status_code=204)
def discard_shared_comment_file(
    publication_id: UUID, file_id: UUID, db: Db, user: SharedReader
):
    comments.files.discard(db, user, publication_id, file_id)


@shared_router.get(
    "/{publication_id}/comments/{comment_id}/files/{file_id}/content",
    response_class=Response,
)
def shared_comment_file_content(
    publication_id: UUID,
    comment_id: UUID,
    file_id: UUID,
    db: Db,
    user: SharedReader,
    inline: bool = False,
):
    content, source = comments.files.content(
        db, user, publication_id, comment_id, file_id, inline=inline
    )
    return _file_response(content, source, inline)


@router.post(
    "/artifact-publications/{publication_id}/comments/files/plan",
    response_model=FileUploadPlan,
)
def plan_publication_comment_file(
    project_id: int, publication_id: UUID, data: FileUploadRequest, db: Db, user: Reader
):
    return comments.files.plan(db, user, publication_id, data, project_id)


@router.post(
    "/artifact-publications/{publication_id}/comments/files/complete",
    response_model=SharedCommentFileRead,
    status_code=201,
)
def complete_publication_comment_file(
    project_id: int,
    publication_id: UUID,
    data: FileUploadComplete,
    db: Db,
    user: Reader,
):
    return comments.files.complete(
        db, user, publication_id, data.upload_token, project_id
    )


@router.post(
    "/artifact-publications/{publication_id}/comments/files",
    response_model=SharedCommentFileRead,
    status_code=201,
)
def upload_publication_comment_file(
    project_id: int,
    publication_id: UUID,
    db: Db,
    user: Reader,
    file: CommentUpload,
    request_id: UploadNonce,
):
    return comments.files.upload(db, user, publication_id, file, request_id, project_id)


@router.delete(
    "/artifact-publications/{publication_id}/comments/files/{file_id}", status_code=204
)
def discard_publication_comment_file(
    project_id: int, publication_id: UUID, file_id: UUID, db: Db, user: Reader
):
    comments.files.discard(db, user, publication_id, file_id, project_id)


@router.get(
    "/artifact-publications/{publication_id}/comments/{comment_id}/files/{file_id}/content",
    response_class=Response,
)
def publication_comment_file_content(
    project_id: int,
    publication_id: UUID,
    comment_id: UUID,
    file_id: UUID,
    db: Db,
    user: Reader,
    inline: bool = False,
):
    content, source = comments.files.content(
        db, user, publication_id, comment_id, file_id, project_id, inline=inline
    )
    return _file_response(content, source, inline)


@router.get(
    "/artifact-publications/{publication_id}/comments",
    response_model=SharedPublicationCommentList,
)
def publication_shared_comments(
    project_id: int,
    publication_id: UUID,
    db: Db,
    user: Reader,
    parent_id: UUID | None = None,
    page: Page = 1,
    page_size: PageSize = 20,
    thread_id: UUID | None = None,
):
    """社内の固定版閲覧画面へ共有スレッドを表示する。"""
    return comments.listing(
        db,
        user,
        publication_id,
        parent_id,
        page,
        page_size,
        project_id,
        thread_id=thread_id,
    )


@router.post(
    "/artifact-publications/{publication_id}/comments",
    response_model=SharedPublicationCommentRead,
    status_code=201,
)
def add_publication_shared_comment(
    project_id: int,
    publication_id: UUID,
    data: SharedPublicationCommentCreate,
    db: Db,
    user: Publisher,
):
    """社内担当者が明示的に選んだ本文を共有スレッドに投稿する。"""
    return comments.create(db, user, publication_id, data, project_id)
