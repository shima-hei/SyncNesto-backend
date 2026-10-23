"""内部の現在内容への合意をまとめて取得する新しい読取API。"""

from fastapi import APIRouter, Depends, Query, Response
from pydantic import PositiveInt
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.requirement_agreement import RequirementAgreementPage
from app.services.requirement_agreement import RequirementAgreementService

router = APIRouter(
    prefix="/projects/{project_id}/requirement-agreements",
    tags=["requirement-agreements"],
)
service = RequirementAgreementService()


@router.get("", response_model=RequirementAgreementPage)
def read_requirement_agreements(
    project_id: int,
    response: Response,
    requirement_ids: list[PositiveInt] = Query(min_length=1, max_length=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_project_permission("project:read")),
):
    """全件/履歴全文ではなく、表示対象IDと取得時点の状態を返す。"""
    response.headers["Cache-Control"] = "no-store"
    return service.read(db, user, project_id, requirement_ids)
