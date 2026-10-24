"""概要の日程参照専用API。"""

from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.requirement_schedule import RequirementSchedule
from app.services.requirement_schedule import RequirementScheduleService

router = APIRouter(tags=["requirement-schedule"])


@router.get(
    "/projects/{project_id}/requirement-schedule", response_model=RequirementSchedule
)
def read_requirement_schedule(
    project_id: int,
    response: Response,
    page: int = Query(default=1, ge=1),
    common_page: int = Query(default=1, ge=1),
    document_id: int | None = Query(default=None, gt=0),
    q: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None, max_length=50),
    window_start: date | None = None,
    window_end: date | None = None,
    metadata_only: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_project_permission("project:read")),
):
    """最初はmetadata_onlyで利用可否だけを確認できる。"""
    response.headers["Cache-Control"] = "no-store"
    return RequirementScheduleService().read(
        db,
        user,
        project_id,
        page,
        common_page,
        document_id,
        q,
        status,
        window_start,
        window_end,
        metadata_only,
    )
