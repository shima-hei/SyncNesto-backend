"""テストケースの不具合関連と進捗集計API。"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.test_issue import (
    IssueTestCaseRead,
    TestCaseIssueCreate,
    TestCaseIssueRead,
    TestProgressRead,
)
from app.services.test_issue import TestIssueService

router = APIRouter(prefix="/projects/{project_id}", tags=["test-issues"])
service = TestIssueService()


@router.get(
    "/test-designs/{design_id}/issues", response_model=list[TestCaseIssueRead]
)
def list_design_issues(
    project_id: int,
    design_id: int,
    _: User = Depends(require_project_permission("test_case:read")),
    _task: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
) -> list[TestCaseIssueRead]:
    """設計書内の不具合関連を一括で返す。"""
    return service.for_design(db, project_id, design_id)


@router.get(
    "/test-designs/{design_id}/cases/{case_id}/issues",
    response_model=list[TestCaseIssueRead],
)
def list_case_issues(
    project_id: int,
    design_id: int,
    case_id: UUID,
    _: User = Depends(require_project_permission("test_case:read")),
    _task: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
) -> list[TestCaseIssueRead]:
    """ケースに関連する不具合を返す。"""
    return service.for_case(db, project_id, design_id, case_id)


@router.post(
    "/test-designs/{design_id}/cases/{case_id}/issues",
    response_model=TestCaseIssueRead,
    status_code=201,
)
def link_case_issue(
    project_id: int,
    design_id: int,
    case_id: UUID,
    data: TestCaseIssueCreate,
    user: User = Depends(require_project_permission("test_case:execute")),
    _task: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
) -> TestCaseIssueRead:
    """同一プロジェクトの不具合タスクを紐付ける。"""
    return service.create(db, project_id, design_id, case_id, data, user.id)


@router.delete(
    "/test-designs/{design_id}/cases/{case_id}/issues/{link_id}", status_code=204
)
def unlink_case_issue(
    project_id: int,
    design_id: int,
    case_id: UUID,
    link_id: int,
    _: User = Depends(require_project_permission("test_case:execute")),
    _task: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
) -> Response:
    """不具合本体を残し、指定した関連のみ解除する。"""
    service.delete(db, project_id, design_id, case_id, link_id)
    return Response(status_code=204)


@router.get("/tasks/{task_id}/test-cases", response_model=list[IssueTestCaseRead])
def list_issue_test_cases(
    project_id: int,
    task_id: int,
    _: User = Depends(require_project_permission("task:read")),
    _cases: User = Depends(require_project_permission("test_case:read")),
    db: Session = Depends(get_db),
) -> list[IssueTestCaseRead]:
    """不具合タスクから関連ケースを逆引きする。"""
    return service.for_task(db, project_id, task_id)


@router.get("/test-designs/{design_id}/progress", response_model=TestProgressRead)
def read_test_progress(
    project_id: int,
    design_id: int,
    target_feature: str | None = Query(default=None),
    _: User = Depends(require_project_permission("test_case:read")),
    _task: User = Depends(require_project_permission("task:read")),
    db: Session = Depends(get_db),
) -> TestProgressRead:
    """有効なケースの最新状態と関連不具合を集計する。"""
    return service.progress(db, project_id, design_id, target_feature)
