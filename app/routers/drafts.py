"""下書き保存APIのルーティングを定義するモジュール。"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.draft import DraftListResponse, DraftRead, DraftUpsert
from app.services.draft import DraftService

router = APIRouter(prefix="/drafts", tags=["drafts"])
draft_service = DraftService()


@router.get("", response_model=DraftListResponse)
def list_drafts(
    resource_type: str | None = Query(default=None),
    project_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DraftListResponse:
    """現在のユーザーの下書き一覧を取得する。"""
    drafts = draft_service.list_drafts(
        db,
        current_user=current_user,
        resource_type=resource_type,
        project_id=project_id,
    )
    return DraftListResponse(
        items=[DraftRead.model_validate(draft) for draft in drafts],
        total=len(drafts),
    )


@router.get("/{draft_id}", response_model=DraftRead)
def read_draft(
    draft_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DraftRead:
    """現在のユーザーの下書きを取得する。"""
    return DraftRead.model_validate(
        draft_service.get_draft(
            db,
            current_user=current_user,
            draft_id=draft_id,
        )
    )


@router.put("/{scope_key}", response_model=DraftRead)
def upsert_draft(
    scope_key: str,
    draft_in: DraftUpsert,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DraftRead:
    """scope_key単位で下書きを作成または更新する。"""
    return DraftRead.model_validate(
        draft_service.upsert_draft(
            db,
            current_user=current_user,
            scope_key=scope_key,
            draft_in=draft_in,
        )
    )


@router.delete("/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(
    draft_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """現在のユーザーの下書きを削除する。"""
    draft_service.delete_draft(
        db,
        current_user=current_user,
        draft_id=draft_id,
    )
