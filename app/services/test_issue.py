"""不具合とケースの関連、最新状態による進捗集計。"""

from collections import Counter, defaultdict
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, DuplicateResourceError, NotFoundError
from app.models.test_design import TestCaseIssue
from app.repositories.test_collaboration import TestCollaborationRepository
from app.repositories.test_issue import TestIssueRepository
from app.schemas.test_issue import (
    IssueSummaryRead,
    IssueTestCaseRead,
    TestCaseIssueCreate,
    TestCaseIssueRead,
    TestProgressRead,
)
from app.services.test_design import TestDesignService
from app.services.test_design_cases import case_key, case_sources


class TestIssueService:
    """設計書・タスクの所属を検証して関連を操作する。"""

    def __init__(self) -> None:
        """既存の設計サービスと専用Repositoryを使用する。"""
        self.designs = TestDesignService()
        self.cases = TestCollaborationRepository()
        self.repository = TestIssueRepository()

    @staticmethod
    def _read(link: TestCaseIssue, task) -> TestCaseIssueRead:
        """関連と既存タスクの表示情報を返す。"""
        return TestCaseIssueRead(
            id=link.id,
            case_id=link.case_id,
            task_id=task.id,
            task_code=task.task_code,
            title=task.title,
            status=task.status,
            origin_execution_id=link.origin_execution_id,
            created_at=link.created_at,
        )

    def _case(self, db: Session, project_id: int, design_id: int, case_id: UUID):
        """同一プロジェクト内のケースを検証する。"""
        self.designs.get(db, project_id, design_id)
        case = self.cases.case(db, design_id, case_id)
        if case is None:
            raise NotFoundError()
        return case

    def for_case(
        self, db: Session, project_id: int, design_id: int, case_id: UUID
    ) -> list[TestCaseIssueRead]:
        """一件のケースに関連する不具合を取得する。"""
        self._case(db, project_id, design_id, case_id)
        return [
            self._read(link, task)
            for link, task in self.repository.for_case(db, project_id, case_id)
        ]

    def for_design(
        self, db: Session, project_id: int, design_id: int
    ) -> list[TestCaseIssueRead]:
        """設計書のケースと不具合の関連を一括取得する。"""
        self.designs.get(db, project_id, design_id)
        return [
            self._read(link, task)
            for link, task, _ in self.repository.for_design(db, project_id, design_id)
        ]

    def create(
        self,
        db: Session,
        project_id: int,
        design_id: int,
        case_id: UUID,
        data: TestCaseIssueCreate,
        actor_id: int,
    ) -> TestCaseIssueRead:
        """既存の不具合タスクをケースに関連付ける。"""
        self._case(db, project_id, design_id, case_id)
        task = self.repository.task(db, project_id, data.task_id)
        if task is None:
            raise NotFoundError()
        if data.origin_execution_id and not self.cases.execution(
            db, case_id, data.origin_execution_id
        ):
            raise BadRequestError("起票元の実行履歴がケースに属していません")
        if self.repository.pair(db, case_id, task.id):
            raise DuplicateResourceError("この不具合は既に関連付けられています")
        link = TestCaseIssue(
            case_id=case_id,
            task_id=task.id,
            origin_execution_id=data.origin_execution_id,
            created_by=actor_id,
        )
        db.add(link)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise DuplicateResourceError(
                "この不具合は既に関連付けられています"
            ) from exc
        db.refresh(link)
        return self._read(link, task)

    def delete(
        self, db: Session, project_id: int, design_id: int, case_id: UUID, link_id: int
    ) -> None:
        """指定した関連だけを解除し、タスク本体は残す。"""
        self._case(db, project_id, design_id, case_id)
        link = self.repository.link(db, case_id, link_id)
        if link is None:
            raise NotFoundError()
        db.delete(link)
        db.commit()

    def for_task(
        self, db: Session, project_id: int, task_id: int
    ) -> list[IssueTestCaseRead]:
        """不具合タスクから関連ケースを逆引きする。"""
        if self.repository.task(db, project_id, task_id) is None:
            raise NotFoundError()
        return [
            IssueTestCaseRead(
                link_id=link.id,
                case_id=case.id,
                design_id=design.id,
                design_name=design.name,
                item_code=case.source.get("item", {}).get("code", ""),
                item_content=case.source.get("item", {}).get("content", ""),
                pattern_code=(case.source.get("pattern") or {}).get("code"),
                status=case.status,
                origin_execution_id=link.origin_execution_id,
            )
            for link, case, design in self.repository.for_task(db, project_id, task_id)
        ]

    def progress(
        self,
        db: Session,
        project_id: int,
        design_id: int,
        target_feature: str | None = None,
    ) -> TestProgressRead:
        """有効なケースの最新状態だけを集計する。"""
        design = self.designs.get(db, project_id, design_id)
        sources = case_sources(self.designs.read(db, design))
        active = {
            case.id: case
            for case in self.designs.repository.cases(db, design_id)
            if (source := sources.get(case_key(case))) is not None
            and (
                target_feature is None
                or source["item"].get("target_feature", "") == target_feature
            )
        }
        counts = Counter(case.status for case in active.values())
        links = [
            (link, task, case)
            for link, task, case in self.repository.for_design(
                db, project_id, design_id
            )
            if case.id in active
        ]
        issue_cases: dict[int, set[UUID]] = defaultdict(set)
        issue_tasks = {}
        linked_cases: set[UUID] = set()
        for _, task, case in links:
            issue_cases[task.id].add(case.id)
            issue_tasks[task.id] = task
            linked_cases.add(case.id)
        issues = [
            IssueSummaryRead(
                task_id=task_id,
                task_code=task.task_code,
                title=task.title,
                status=task.status,
                case_count=len(case_ids),
                failed_case_count=sum(active[id].status == "failed" for id in case_ids),
            )
            for task_id, case_ids in issue_cases.items()
            for task in [issue_tasks[task_id]]
        ]
        issues.sort(key=lambda issue: issue.task_code)
        passed, failed = counts["passed"], counts["failed"]
        return TestProgressRead(
            design_id=design_id,
            target_feature=target_feature,
            total=len(active),
            not_run=counts["not_run"],
            in_progress=counts["in_progress"],
            passed=passed,
            failed=failed,
            blocked=counts["blocked"],
            not_applicable=counts["not_applicable"],
            progress_numerator=passed + failed,
            progress_denominator=len(active) - counts["not_applicable"],
            ng_numerator=failed,
            ng_denominator=passed + failed,
            issue_count=len(issues),
            failed_without_issue=sum(
                case.status == "failed" and case.id not in linked_cases
                for case in active.values()
            ),
            issues=issues,
        )
