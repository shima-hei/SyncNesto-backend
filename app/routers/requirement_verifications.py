"""要件検証の準備専用API。CSRFは既存ミドルウェアで検証する。"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.requirement_verification import (
    VerificationCandidatePage,
    VerificationCreate,
    VerificationDetail,
    VerificationList,
    VerificationRevisionPage,
    VerificationUpdate,
)
from app.services.requirement_verification import RequirementVerificationService


def private_response(response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


router = APIRouter(
    prefix="/projects/{project_id}/requirement-verification-plans",
    tags=["requirement-verifications"],
    dependencies=[Depends(private_response)],
)
service = RequirementVerificationService()
actor = require_project_permission("project:read")


@router.get("", response_model=VerificationList)
def list_verification_plans(
    project_id: int,
    requirement_id: int | None = Query(default=None, gt=0),
    cursor: str | None = Query(default=None, max_length=1000),
    page_size: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.page(db, user, project_id, requirement_id, cursor, page_size)


@router.post("", response_model=VerificationDetail, status_code=201)
def create_verification_plan(
    project_id: int,
    data: VerificationCreate,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    result, created = service.create(db, user, project_id, data)
    response.status_code = 201 if created else 200
    return result


@router.get("/{plan_id}", response_model=VerificationDetail)
def get_verification_plan(
    project_id: int,
    plan_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.get(db, user, project_id, plan_id)


@router.put("/{plan_id}", response_model=VerificationDetail)
def update_verification_plan(
    project_id: int,
    plan_id: UUID,
    data: VerificationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.update(db, user, project_id, plan_id, data)


@router.get("/{plan_id}/revisions", response_model=VerificationRevisionPage)
def list_verification_revisions(
    project_id: int,
    plan_id: UUID,
    cursor: str | None = Query(default=None, max_length=1000),
    page_size: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.revisions(db, user, project_id, plan_id, cursor, page_size)


@router.get("/{plan_id}/execution-candidates", response_model=VerificationCandidatePage)
def list_verification_execution_candidates(
    project_id: int,
    plan_id: UUID,
    row_id: UUID | None = None,
    browse_patterns: bool = False,
    cursor: str | None = Query(default=None, max_length=1000),
    page_size: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(actor),
):
    return service.execution_candidates(
        db, user, project_id, plan_id, row_id, cursor, page_size, browse_patterns
    )
