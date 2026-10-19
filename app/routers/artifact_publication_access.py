"""内部の公開操作・参加管理と、本人限定sharedの取得経路。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_project_permission
from app.core.tenant import require_project_management
from app.db.session import get_db
from app.models.user import User
from app.schemas.artifact_publication_access import (
    GuestParticipationList,
    PublicationEventList,
    PublicationGrantList,
    PublicationOperationResult,
    PublicationPublish,
    PublicationRevoke,
    SharedPublicationFiles,
    SharedPublicationList,
    SharedPublicationNodePage,
    SharedPublicationRead,
)
from app.schemas.artifact_release import SourceKind
from app.services.artifact_publication_access import ArtifactPublicationAccessService
from app.services.shared_publication import SharedPublicationService

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
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=1000)]


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


@shared_router.get("", response_model=SharedPublicationList)
def shared_publications(
    db: Db,
    user: SharedReader,
    q: str = Query(default="", max_length=200),
    kind: SourceKind | None = None,
    cursor: Cursor = None,
    page_size: PageSize = 20,
):
    """認証済み本人へ現在許可された集合だけを返す。"""
    return shared_service.listing(db, user, q, kind, cursor, page_size)


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
