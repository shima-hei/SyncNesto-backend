"""要件定義変更履歴APIのルーティングを定義するモジュール。"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.routers import requirements_shared as shared
from app.schemas.requirement import RequirementChangeLogListResponse

router = APIRouter(prefix="/projects/{project_id}", tags=["requirements"])


@router.get(
    "/change-logs",
    response_model=RequirementChangeLogListResponse,
)
def list_requirement_change_logs(
    project_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    document_id: int | None = Query(default=None),
    target_type: str | None = Query(default=None),
    target_id: int | None = Query(default=None),
    action: str | None = Query(default=None),
    changed_by: int | None = Query(default=None),
    changed_at_from: datetime | None = Query(default=None),
    changed_at_to: datetime | None = Query(default=None),
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> RequirementChangeLogListResponse:
    """要件定義変更履歴一覧を取得する。

    Args:
        project_id: 一覧取得対象のプロジェクトID。
        page: 取得ページ番号。
        page_size: 1ページあたりの取得件数。
        document_id: 絞り込み対象の要件定義書ID。
        target_type: 絞り込み対象の変更対象種別。
        target_id: 絞り込み対象の変更対象ID。
        action: 絞り込み対象の操作種別。
        changed_by: 絞り込み対象の変更ユーザーID。
        changed_at_from: 変更日時の開始日時。
        changed_at_to: 変更日時の終了日時。
        _: 認可済みユーザー。
        db: DBセッション。

    Returns:
        要件定義変更履歴のページング済み一覧。
    """
    change_logs, total = shared.change_log_service.list_change_logs_paginated(
        db,
        project_id=project_id,
        page=page,
        page_size=page_size,
        document_id=document_id,
        target_type=target_type,
        target_id=target_id,
        action=action,
        changed_by=changed_by,
        changed_at_from=changed_at_from,
        changed_at_to=changed_at_to,
    )
    return RequirementChangeLogListResponse(
        items=shared.change_log_service.build_change_log_reads(db, change_logs),
        total=total,
        page=page,
        page_size=page_size,
    )
