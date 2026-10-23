"""要件追跡の読取専用API。既存の更新/判断には接続しない。"""

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.requirement_trace import RequirementTraceDetail, RequirementTracePage
from app.services.requirement_trace import RequirementTraceService

router = APIRouter(
    prefix="/projects/{project_id}/requirement-trace", tags=["requirement-trace"]
)
service = RequirementTraceService()


@router.get("", response_model=RequirementTracePage)
def read_requirement_trace(
    project_id: int,
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    document_id: int | None = Query(default=None, gt=0),
    q: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None, max_length=50),
    db: Session = Depends(get_db),
    user: User = Depends(require_project_permission("project:read")),
):
    """表示pageに必要な要件と関連件数だけを読む。"""
    response.headers["Cache-Control"] = "no-store"
    return service.page(db, user, project_id, page, page_size, document_id, q, status)


@router.get("/{requirement_id}", response_model=RequirementTraceDetail)
def read_requirement_trace_detail(
    project_id: int,
    requirement_id: int,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_project_permission("requirement:read")),
):
    """選択した1要件の抜粋と上限付き関連を読む。"""
    response.headers["Cache-Control"] = "no-store"
    return service.detail(db, user, project_id, requirement_id)
