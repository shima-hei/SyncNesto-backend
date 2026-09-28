"""テスト設計の要件関連、コメント、実行証跡API。"""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.auth import require_project_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.test_collaboration import (
    RequirementCoverageRead,
    RequirementTestItemCreate,
    RequirementTestItemRead,
    TestDesignCommentChangeRead,
    TestDesignCommentCreate,
    TestDesignCommentRead,
    TestDesignCommentUpdate,
    TestEvidenceDownload,
    TestEvidenceRead,
    TestExecutionRead,
)
from app.services.authorization import AuthorizationService
from app.services.test_collaboration import TestCollaborationService
from app.services.test_evidence import MAX_BYTES, TestEvidenceService

router = APIRouter(prefix="/projects/{project_id}", tags=["test-collaboration"])
service = TestCollaborationService()
evidence_service = TestEvidenceService()
authorization = AuthorizationService()


@router.get(
    "/test-designs/{design_id}/requirement-links",
    response_model=list[RequirementTestItemRead],
)
def list_design_requirement_links(
    project_id: int,
    design_id: int,
    _: User = Depends(require_project_permission("test_plan:read")),
    db: Session = Depends(get_db),
) -> list[RequirementTestItemRead]:
    """テスト設計書から関連要件を取得する。"""
    return service.list_design_links(db, project_id, design_id)


@router.post(
    "/test-designs/{design_id}/requirement-links",
    response_model=RequirementTestItemRead,
    status_code=201,
)
def create_design_requirement_link(
    project_id: int,
    design_id: int,
    data: RequirementTestItemCreate,
    user: User = Depends(require_project_permission("requirement:link")),
    db: Session = Depends(get_db),
) -> RequirementTestItemRead:
    """要件とテスト項目を関連付ける。"""
    return service.create_link(db, project_id, design_id, data, user.id)


@router.delete("/test-designs/{design_id}/requirement-links/{link_id}", status_code=204)
def delete_design_requirement_link(
    project_id: int,
    design_id: int,
    link_id: int,
    _: User = Depends(require_project_permission("requirement:link")),
    db: Session = Depends(get_db),
) -> Response:
    """指定した関連だけを解除する。"""
    service.delete_link(db, project_id, design_id, link_id)
    return Response(status_code=204)


@router.get(
    "/requirements/{requirement_id}/test-items",
    response_model=list[RequirementTestItemRead],
)
def list_requirement_test_items(
    project_id: int,
    requirement_id: int,
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> list[RequirementTestItemRead]:
    """要件から関連するテスト項目を取得する。"""
    return service.list_requirement_links(db, project_id, requirement_id)


@router.get("/requirement-test-coverage", response_model=list[RequirementCoverageRead])
def requirement_test_coverage(
    project_id: int,
    document_id: int | None = Query(default=None),
    _: User = Depends(require_project_permission("requirement:read")),
    db: Session = Depends(get_db),
) -> list[RequirementCoverageRead]:
    """関連テスト項目の有無を要件ごとに返す。"""
    return service.coverage(db, project_id, document_id)


@router.get(
    "/test-designs/{design_id}/comments", response_model=list[TestDesignCommentRead]
)
def list_test_design_comments(
    project_id: int,
    design_id: int,
    _: User = Depends(require_project_permission("test_plan:read")),
    db: Session = Depends(get_db),
) -> list[TestDesignCommentRead]:
    """設計書内のコメントを取得する。"""
    return service.list_comments(db, project_id, design_id)


@router.post(
    "/test-designs/{design_id}/comments",
    response_model=TestDesignCommentRead,
    status_code=201,
)
def create_test_design_comment(
    project_id: int,
    design_id: int,
    data: TestDesignCommentCreate,
    user: User = Depends(require_project_permission("test_plan:comment")),
    db: Session = Depends(get_db),
) -> TestDesignCommentRead:
    """設計対象へコメントまたは返信を投稿する。"""
    return service.create_comment(db, project_id, design_id, data, user.id)


@router.patch(
    "/test-designs/{design_id}/comments/{comment_id}",
    response_model=TestDesignCommentRead,
)
def update_test_design_comment(
    project_id: int,
    design_id: int,
    comment_id: int,
    data: TestDesignCommentUpdate,
    user: User = Depends(require_project_permission("test_plan:comment")),
    db: Session = Depends(get_db),
) -> TestDesignCommentRead:
    """コメント本文または解決状態を更新する。"""
    can_moderate = authorization.has_system_permission(
        db, user=user, permission_code="test_plan:comment"
    )
    return service.update_comment(
        db, project_id, design_id, comment_id, data, user.id, can_moderate
    )


@router.delete(
    "/test-designs/{design_id}/comments/{comment_id}",
    response_model=TestDesignCommentRead,
)
def delete_test_design_comment(
    project_id: int,
    design_id: int,
    comment_id: int,
    version: int = Query(ge=1),
    user: User = Depends(require_project_permission("test_plan:comment")),
    db: Session = Depends(get_db),
) -> TestDesignCommentRead:
    """投稿者またはモデレーターがコメントを論理削除する。"""
    can_moderate = authorization.has_system_permission(
        db, user=user, permission_code="test_plan:comment"
    )
    return service.update_comment(
        db,
        project_id,
        design_id,
        comment_id,
        TestDesignCommentUpdate(version=version),
        user.id,
        can_moderate,
        delete=True,
    )


@router.get(
    "/test-designs/{design_id}/comments/{comment_id}/changes",
    response_model=list[TestDesignCommentChangeRead],
)
def list_test_design_comment_changes(
    project_id: int,
    design_id: int,
    comment_id: int,
    _: User = Depends(require_project_permission("test_plan:read")),
    db: Session = Depends(get_db),
) -> list[TestDesignCommentChangeRead]:
    """コメントの変更履歴を取得する。"""
    return service.list_comment_changes(db, project_id, design_id, comment_id)


@router.get(
    "/test-designs/{design_id}/cases/{case_id}/executions",
    response_model=list[TestExecutionRead],
)
def list_test_executions(
    project_id: int,
    design_id: int,
    case_id: UUID,
    _: User = Depends(require_project_permission("test_case:read")),
    db: Session = Depends(get_db),
) -> list[TestExecutionRead]:
    """ケースの実行履歴を取得する。"""
    return evidence_service.list_executions(db, project_id, design_id, case_id)


@router.get(
    "/test-designs/{design_id}/cases/{case_id}/executions/{execution_id}/evidence",
    response_model=list[TestEvidenceRead],
)
def list_test_evidence(
    project_id: int,
    design_id: int,
    case_id: UUID,
    execution_id: UUID,
    _: User = Depends(require_project_permission("test_case:read")),
    db: Session = Depends(get_db),
) -> list[TestEvidenceRead]:
    """一回の実行に属する証跡を取得する。"""
    return evidence_service.list_evidence(
        db, project_id, design_id, case_id, execution_id
    )


@router.post(
    "/test-designs/{design_id}/cases/{case_id}/executions/{execution_id}/evidence",
    response_model=TestEvidenceRead,
    status_code=201,
)
def upload_test_evidence(
    project_id: int,
    design_id: int,
    case_id: UUID,
    execution_id: UUID,
    file: UploadFile = File(...),
    user: User = Depends(require_project_permission("test_case:execute")),
    db: Session = Depends(get_db),
) -> TestEvidenceRead:
    """容量を制限して非公開ストレージへ証跡を保存する。"""
    content = file.file.read(MAX_BYTES + 1)
    return evidence_service.upload(
        db,
        project_id,
        design_id,
        case_id,
        execution_id,
        file.filename or "",
        file.content_type or "",
        content,
        user.id,
    )


@router.get(
    "/test-designs/{design_id}/cases/{case_id}/executions/{execution_id}/evidence/{evidence_id}/download",
    response_model=TestEvidenceDownload,
)
def download_test_evidence(
    project_id: int,
    design_id: int,
    case_id: UUID,
    execution_id: UUID,
    evidence_id: UUID,
    _: User = Depends(require_project_permission("test_case:read")),
    db: Session = Depends(get_db),
) -> TestEvidenceDownload:
    """権限検証済みの一時閲覧URLを返す。"""
    return TestEvidenceDownload(
        url=evidence_service.download_url(
            db, project_id, design_id, case_id, execution_id, evidence_id
        )
    )


@router.delete(
    "/test-designs/{design_id}/cases/{case_id}/executions/{execution_id}/evidence/{evidence_id}",
    status_code=204,
)
def delete_test_evidence(
    project_id: int,
    design_id: int,
    case_id: UUID,
    execution_id: UUID,
    evidence_id: UUID,
    _: User = Depends(require_project_permission("test_case:execute")),
    db: Session = Depends(get_db),
) -> Response:
    """明示的に指定された証跡を一覧から削除する。"""
    evidence_service.delete(
        db, project_id, design_id, case_id, execution_id, evidence_id
    )
    return Response(status_code=204)
