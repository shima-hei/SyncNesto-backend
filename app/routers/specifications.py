"""設計書のProject認可、版、レビューと関連付け。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.specification import (
    SpecificationCommentCreate,
    SpecificationCommentRead,
    SpecificationCommentUpdate,
    SpecificationCreate,
    SpecificationLinkCreate,
    SpecificationLinkRead,
    SpecificationList,
    SpecificationListItem,
    SpecificationRead,
    SpecificationRevisionRead,
    SpecificationRevisionSummary,
    SpecificationStatusUpdate,
    SpecificationTarget,
    SpecificationUpdate,
)
from app.services.specification import SpecificationService

router = APIRouter(
    prefix="/projects/{project_id}/specifications", tags=["specifications"]
)
service = SpecificationService()
Db = Annotated[Session, Depends(get_db)]
Reader = Annotated[User, Depends(require_project_permission("document:read"))]
Creator = Annotated[User, Depends(require_project_permission("document:create"))]
Editor = Annotated[User, Depends(require_project_permission("document:update"))]
Deleter = Annotated[User, Depends(require_project_permission("document:delete"))]


@router.get("", response_model=SpecificationList)
def list_specifications(
    project_id: int,
    db: Db,
    user: Reader,
    kind: Literal["api", "screen"] | None = None,
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """一覧を種類・名前・目的で絞り込む。"""
    return service.list(db, project_id, kind, q, page, page_size)


@router.post("", response_model=SpecificationRead, status_code=201)
def create_specification(
    project_id: int, data: SpecificationCreate, db: Db, user: Creator
):
    """最小の入力から下書きを作成する。"""
    return service.create(db, project_id, data, user)


@router.get("/link-candidates", response_model=list[SpecificationLinkRead])
def specification_link_candidates(
    project_id: int,
    target_type: SpecificationTarget,
    db: Db,
    user: Reader,
    q: str = Query(default="", max_length=200),
):
    """閲覧できる関連先だけを検索する。最大50件。"""
    return service.candidates(db, project_id, target_type, q, user)


@router.get("/related", response_model=list[SpecificationListItem])
def related_specifications(
    project_id: int,
    target_type: SpecificationTarget,
    db: Db,
    user: Reader,
    target_id: str = Query(min_length=1, max_length=50),
):
    """要件・タスク・テストから逆引きする。"""
    return service.related(db, project_id, target_type, target_id, user)


@router.get("/{specification_id}", response_model=SpecificationRead)
def read_specification(project_id: int, specification_id: int, db: Db, user: Reader):
    """現在の版と本文を取得する。"""
    return service.get(db, project_id, specification_id)


@router.patch("/{specification_id}", response_model=SpecificationRead)
def update_specification(
    project_id: int,
    specification_id: int,
    data: SpecificationUpdate,
    db: Db,
    user: Editor,
):
    """他の編集を上書きしない版付き更新。"""
    return service.update(db, project_id, specification_id, data, user)


@router.patch("/{specification_id}/status", response_model=SpecificationRead)
def update_specification_status(
    project_id: int,
    specification_id: int,
    data: SpecificationStatusUpdate,
    db: Db,
    user: Editor,
):
    """レビュー依頼・差し戻し・承認を記録する。"""
    return service.status(
        db, project_id, specification_id, data.version, data.status, user
    )


@router.delete("/{specification_id}", status_code=204)
def delete_specification(
    project_id: int,
    specification_id: int,
    db: Db,
    user: Deleter,
    version: int = Query(ge=1),
):
    """既存ごみ箱で復元できる論理削除。"""
    service.delete(db, project_id, specification_id, version, user)
    return Response(status_code=204)


@router.get(
    "/{specification_id}/revisions", response_model=list[SpecificationRevisionSummary]
)
def specification_revisions(
    project_id: int, specification_id: int, db: Db, user: Reader
):
    """最新100版の履歴を取得する。"""
    row = service.get(db, project_id, specification_id)
    return service.repository.revisions(db, row.id)


@router.get(
    "/{specification_id}/revisions/{number}", response_model=SpecificationRevisionRead
)
def read_specification_revision(
    project_id: int, specification_id: int, number: int, db: Db, user: Reader
):
    """選択した1版だけを取得する。"""
    return service.revision(db, project_id, specification_id, number)


@router.get(
    "/{specification_id}/comments", response_model=list[SpecificationCommentRead]
)
def specification_comments(
    project_id: int,
    specification_id: int,
    db: Db,
    user: Reader,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=100),
):
    """指摘箇所の変更も通知する。"""
    return service.comments(db, project_id, specification_id, page, page_size)


@router.post(
    "/{specification_id}/comments",
    response_model=SpecificationCommentRead,
    status_code=201,
)
def create_specification_comment(
    project_id: int,
    specification_id: int,
    data: SpecificationCommentCreate,
    db: Db,
    user: Editor,
):
    """確認した版の項目へ1件の指摘を投稿する。"""
    return service.comment(db, project_id, specification_id, data, user)


@router.patch(
    "/{specification_id}/comments/{comment_id}", response_model=SpecificationCommentRead
)
def update_specification_comment(
    project_id: int,
    specification_id: int,
    comment_id: int,
    data: SpecificationCommentUpdate,
    db: Db,
    user: Editor,
):
    """指摘を解決・再オープンする。"""
    return service.resolve(
        db,
        project_id,
        specification_id,
        comment_id,
        data.version,
        data.is_resolved,
        user,
    )


@router.get("/{specification_id}/links", response_model=list[SpecificationLinkRead])
def specification_links(project_id: int, specification_id: int, db: Db, user: Reader):
    """閲覧できる関連先の名前と移動先だけを返す。"""
    return service.links(db, project_id, specification_id, user)


@router.post(
    "/{specification_id}/links",
    response_model=list[SpecificationLinkRead],
    status_code=201,
)
def create_specification_link(
    project_id: int,
    specification_id: int,
    data: SpecificationLinkCreate,
    db: Db,
    user: Editor,
):
    """同一Project内の資源を選択して関連付ける。"""
    return service.add_link(db, project_id, specification_id, data, user)


@router.delete("/{specification_id}/links/{link_id}", status_code=204)
def delete_specification_link(
    project_id: int, specification_id: int, link_id: int, db: Db, user: Editor
):
    """関連だけを解除し、参照先のデータは保持する。"""
    service.delete_link(db, project_id, specification_id, link_id, user)
    return Response(status_code=204)
