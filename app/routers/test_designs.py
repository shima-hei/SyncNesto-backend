"""プロジェクト権限で保護されたテスト設計API。"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.test_design import (
    CaseGenerateRequest,
    TestCaseRead,
    TestCaseUpdate,
    TestDesignCreate,
    TestDesignRead,
    TestDesignSummary,
    TestDesignUpdate,
)
from app.services.test_design import TestDesignService

router = APIRouter(prefix="/projects/{project_id}/test-designs", tags=["test-designs"])
service = TestDesignService()


@router.get("", response_model=list[TestDesignSummary])
def list_test_designs(
    project_id: int,
    _: User = Depends(require_project_permission("test_plan:read")),
    db: Session = Depends(get_db),
) -> list[TestDesignSummary]:
    """テスト設計一覧を取得する。"""
    return service.list_designs(db, project_id)


@router.post("", response_model=TestDesignRead, status_code=201)
def create_test_design(
    project_id: int,
    data: TestDesignCreate,
    user: User = Depends(require_project_permission("test_plan:create")),
    db: Session = Depends(get_db),
) -> TestDesignRead:
    """空の設計書を作成する。"""
    return service.create(db, project_id, data, user.id)


@router.get("/{design_id}", response_model=TestDesignRead)
def read_test_design(
    project_id: int,
    design_id: int,
    _: User = Depends(require_project_permission("test_plan:read")),
    db: Session = Depends(get_db),
) -> TestDesignRead:
    """構造化された設計書を取得する。"""
    return service.read(db, service.get(db, project_id, design_id))


@router.put("/{design_id}", response_model=TestDesignRead)
def update_test_design(
    project_id: int,
    design_id: int,
    data: TestDesignUpdate,
    user: User = Depends(require_project_permission("test_plan:update")),
    db: Session = Depends(get_db),
) -> TestDesignRead:
    """表編集を原子的に保存する。"""
    return service.update(db, project_id, design_id, data, user.id)


@router.delete("/{design_id}", status_code=204)
def delete_test_design(
    project_id: int,
    design_id: int,
    version: int = Query(ge=1),
    user: User = Depends(require_project_permission("test_plan:delete")),
    db: Session = Depends(get_db),
) -> Response:
    """設計書を削除する。"""
    service.delete(db, project_id, design_id, version, user.id)
    return Response(status_code=204)


@router.get("/{design_id}/cases", response_model=list[TestCaseRead])
def list_test_cases(
    project_id: int,
    design_id: int,
    _: User = Depends(require_project_permission("test_case:read")),
    db: Session = Depends(get_db),
) -> list[TestCaseRead]:
    """ケースと変更影響を取得する。"""
    return service.list_cases(db, project_id, design_id)


@router.post("/{design_id}/cases/generate", response_model=list[TestCaseRead])
def generate_test_cases(
    project_id: int,
    design_id: int,
    data: CaseGenerateRequest,
    user: User = Depends(require_project_permission("test_case:create")),
    db: Session = Depends(get_db),
) -> list[TestCaseRead]:
    """有効な紐付けをケース化する。"""
    return service.generate(db, project_id, design_id, data.version, user.id)


@router.patch("/{design_id}/cases/{case_id}", response_model=TestCaseRead)
def update_test_case(
    project_id: int,
    design_id: int,
    case_id: UUID,
    data: TestCaseUpdate,
    user: User = Depends(require_project_permission("test_case:execute")),
    db: Session = Depends(get_db),
) -> TestCaseRead:
    """ケースの実行結果を保存する。"""
    return service.update_case(db, project_id, design_id, case_id, data, user.id)


@router.post("/{design_id}/cases/{case_id}/refresh", response_model=TestCaseRead)
def refresh_test_case(
    project_id: int,
    design_id: int,
    case_id: UUID,
    data: CaseGenerateRequest,
    user: User = Depends(require_project_permission("test_case:update")),
    db: Session = Depends(get_db),
) -> TestCaseRead:
    """設計変更を明示的にケースへ取り込む。"""
    return service.refresh_case(
        db, project_id, design_id, case_id, data.version, user.id
    )
