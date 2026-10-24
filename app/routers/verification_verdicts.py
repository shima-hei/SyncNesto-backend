"""固定要件の最終判断。旧承認と別の12操作。"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.routers.requirement_verifications import private_response
from app.schemas import verification_verdict as s
from app.services.verification_verdict import VerificationVerdictService

router = APIRouter(
    prefix="/projects/{project_id}",
    tags=["verification-verdicts"],
    dependencies=[Depends(private_response)],
)
base = "/requirement-verification-requests"
service = VerificationVerdictService()
actor = require_project_permission("project:read")


@router.get(base, response_model=s.VerdictPage)
def list_verification_requests(
    project_id: int,
    requirement_id: int | None = Query(default=None, gt=0),
    cursor: str | None = Query(default=None, max_length=1000),
    page_size: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.page(db, user, project_id, requirement_id, cursor, page_size)


@router.post(base, response_model=s.VerdictSummary, status_code=201)
def create_verification_request(
    project_id: int,
    data: s.VerdictCreate,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    result, created = service.create(db, user, project_id, data)
    response.status_code = 201 if created else 200
    return result


@router.post(base + "/preview", response_model=s.VerdictPreview)
def preview_verification_request(
    project_id: int,
    data: s.VerdictSelection,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.preview(db, user, project_id, data)


@router.get(base + "/candidates", response_model=s.VerdictCandidates)
def list_verification_judges(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(actor)
):
    return service.candidates(db, user, project_id)


@router.get(base + "/{request_id}", response_model=s.VerdictDetail)
def get_verification_request(
    project_id: int,
    request_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.detail(db, user, project_id, request_id)


@router.post(base + "/{request_id}/review", response_model=s.VerdictDetail)
def review_verification_request(
    project_id: int,
    request_id: UUID,
    data: s.VerdictReview,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.review(db, user, project_id, request_id, data)


@router.post(base + "/{request_id}/review/advance", response_model=s.VerdictDetail)
def advance_verification_review(
    project_id: int,
    request_id: UUID,
    data: s.VerdictReview,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.advance(db, user, project_id, request_id, data)


@router.post(base + "/{request_id}/decide", response_model=s.VerdictDetail)
def decide_verification_request(
    project_id: int,
    request_id: UUID,
    data: s.VerdictDecision,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.decide(db, user, project_id, request_id, data)


@router.post(base + "/{request_id}/cancel", response_model=s.VerdictDetail)
def cancel_verification_request(
    project_id: int,
    request_id: UUID,
    data: s.VerdictOperation,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.transition(db, user, project_id, request_id, data)


@router.post(base + "/{request_id}/withdraw", response_model=s.VerdictDetail)
def withdraw_verification_decision(
    project_id: int,
    request_id: UUID,
    data: s.VerdictOperation,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.transition(db, user, project_id, request_id, data, withdraw=True)


@router.get(base + "/{request_id}/events", response_model=s.VerdictEvents)
def list_verification_events(
    project_id: int,
    request_id: UUID,
    cursor: str | None = Query(default=None, max_length=1000),
    page_size: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.events(db, user, project_id, request_id, cursor, page_size)


@router.get("/requirement-verdicts", response_model=s.RequirementVerdicts)
def list_requirement_verdicts(
    project_id: int,
    requirement_ids: list[int] = Query(min_length=1, max_length=100),
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.verdicts(db, user, project_id, list(dict.fromkeys(requirement_ids)))
