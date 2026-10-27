"""既存確認表内で使う、証拠提出と保全専用の9操作。"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.routers.requirement_verifications import private_response
from app.schemas import verification_evidence as s
from app.services.verification_evidence import VerificationEvidenceService

router = APIRouter(
    prefix="/projects/{project_id}/requirement-verification-plans/{plan_id}/evidence-sets",
    tags=["verification-evidence"],
    dependencies=[Depends(private_response)],
)
service = VerificationEvidenceService()
actor = require_project_permission("project:read")


@router.post("/preview", response_model=s.EvidencePreview)
def preview_verification_evidence(
    project_id: int,
    plan_id: UUID,
    data: s.EvidenceSelection,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.preview(db, user, project_id, plan_id, data)


@router.get("", response_model=s.EvidencePage)
def list_verification_evidence(
    project_id: int,
    plan_id: UUID,
    plan_version: int | None = Query(default=None, gt=0),
    cursor: str | None = Query(default=None, max_length=1000),
    page_size: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.page(db, user, project_id, plan_id, plan_version, cursor, page_size)


@router.post("", response_model=s.EvidenceSummary, status_code=201)
def create_verification_evidence(
    project_id: int,
    plan_id: UUID,
    data: s.EvidenceCreate,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    result, created = service.create(db, user, project_id, plan_id, data)
    response.status_code = 201 if created else 200
    return result


@router.get("/{set_id}", response_model=s.EvidenceDetail)
def get_verification_evidence(
    project_id: int,
    plan_id: UUID,
    set_id: UUID,
    cursor: str | None = Query(default=None, max_length=1000),
    page_size: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.detail(db, user, project_id, plan_id, set_id, cursor, page_size)


@router.get("/{set_id}/entries/{entry_id}", response_model=s.EvidenceEntryDetail)
def get_verification_evidence_entry(
    project_id: int,
    plan_id: UUID,
    set_id: UUID,
    entry_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.entry(db, user, project_id, plan_id, set_id, entry_id)


@router.post("/{set_id}/advance", response_model=s.EvidenceSummary)
def advance_verification_evidence(
    project_id: int,
    plan_id: UUID,
    set_id: UUID,
    data: s.EvidenceVersion,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.advance(db, user, project_id, plan_id, set_id, data)


@router.post("/{set_id}/cancel", response_model=s.EvidenceSummary)
def cancel_verification_evidence(
    project_id: int,
    plan_id: UUID,
    set_id: UUID,
    data: s.EvidenceVersion,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.cancel(db, user, project_id, plan_id, set_id, data)


@router.post("/{set_id}/cleanup", response_model=s.EvidenceSummary)
def cleanup_verification_evidence(
    project_id: int,
    plan_id: UUID,
    set_id: UUID,
    data: s.EvidenceVersion,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.cleanup(db, user, project_id, plan_id, set_id, data)


@router.post("/{set_id}/files/{file_id}/download", response_model=s.EvidenceDownload)
def download_verification_evidence(
    project_id: int,
    plan_id: UUID,
    set_id: UUID,
    file_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.download(db, user, project_id, plan_id, set_id, file_id)
