"""Project所属と既存の資源別RBACを要求するごみ箱API。"""

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.trash import TrashKind, TrashRead, TrashRestore
from app.services.trash import TrashService

router = APIRouter(prefix="/projects/{project_id}/trash", tags=["trash"])
service = TrashService()


@router.get("", response_model=TrashRead)
def list_trash(
    project_id: int,
    kind: TrashKind | None = None,
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1, le=500),
    page_size: int = Query(default=20, ge=1, le=50),
    user: User = Depends(require_project_permission("project:read")),
    db: Session = Depends(get_db),
) -> TrashRead:
    """削除日時・復元期限・親の状態を本文なしで返す。"""
    return service.list(db, user, project_id, kind, q.strip(), page, page_size)


@router.post("/{kind}/{resource_id}/restore", status_code=204)
def restore_trash(
    project_id: int,
    kind: TrashKind,
    resource_id: str,
    data: TrashRestore,
    user: User = Depends(require_project_permission("project:read")),
    db: Session = Depends(get_db),
) -> Response:
    """期限内の同じ削除世代だけを復元する。"""
    service.restore(db, user, project_id, kind, resource_id, data)
    return Response(status_code=204)
