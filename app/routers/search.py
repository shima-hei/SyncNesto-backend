"""ログインユーザーの権限で保護する横断検索API。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.search import (
    SearchCategory,
    SearchProjectsRead,
    SearchQuery,
    SearchRead,
)
from app.services.search import SearchService

router = APIRouter(prefix="/search", tags=["search"])
service = SearchService()


@router.get("", response_model=SearchRead)
def search_resources(
    q: Annotated[SearchQuery, Query()],
    category: SearchCategory | None = None,
    project_id: int | None = Query(default=None, ge=1),
    page: int = Query(default=1, ge=1, le=500),
    page_size: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SearchRead:
    """所属内の種類別閲覧権限を満たす業務データを検索する。"""
    return service.search(
        db,
        user,
        q=q,
        category=category,
        project_id=project_id,
        page=page,
        page_size=page_size,
    )


@router.get("/projects", response_model=SearchProjectsRead)
def search_projects(
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1, le=500),
    page_size: int = Query(default=50, ge=1, le=50),
    selected_id: int | None = Query(default=None, ge=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SearchProjectsRead:
    """検索できる案件を名前・コードで選択する候補を返す。"""
    return service.projects(
        db, user, q=q.strip(), page=page, page_size=page_size, selected_id=selected_id
    )
