"""既存ドメインの現在値と履歴をプロジェクト概要へ集約する。"""

from collections import Counter, defaultdict
from datetime import date, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.requirement import (
    Requirement,
    RequirementChangeLog,
    RequirementDocument,
)
from app.models.task import Task, TaskChangeLog
from app.models.test_design import TestCase, TestCaseIssue, TestDesign, TestExecution
from app.models.user import User
from app.schemas.project_overview import (
    IssueOverview,
    OverviewAttention,
    ProjectActivity,
    ProjectActivityListRead,
    ProjectOverviewRead,
    RequirementOverview,
    TaskOverview,
    TestOverview,
)
from app.services.authorization import AuthorizationService
from app.services.project import ProjectService
from app.services.test_collaboration import TestCollaborationService
from app.services.test_design import TestDesignService
from app.services.test_design_cases import case_key, case_sources

ATTENTION_LIMIT = 10
ACTIVITY_PREVIEW_LIMIT = 8
DONE_STATUSES = ("done", "cancelled")


class ProjectOverviewService:
    """権限のあるドメインのみ集計する読み取り専用サービス。"""

    def __init__(self) -> None:
        self.authorization = AuthorizationService()
        self.designs = TestDesignService()
        self.collaboration = TestCollaborationService()

    def _can(self, db: Session, user: User, project_id: int, code: str) -> bool:
        return self.authorization.has_project_permission(
            db, user=user, project_id=project_id, permission_code=code
        )

    def read(self, db: Session, project_id: int, user: User) -> ProjectOverviewRead:
        """集計、優先対応、直近の変更を一回のAPI呼び出しで返す。"""
        ProjectService().get_project(db, project_id)
        attention: list[OverviewAttention] = []
        requirements = tasks = tests = issues = None
        today = date.today()

        if self._can(db, user, project_id, "task:read"):
            task_scope = (
                Task.project_id == project_id,
                Task.deleted_at.is_(None),
                Task.task_type != "bug",
            )
            total, done, overdue, blocked = db.execute(
                select(
                    func.count(Task.id),
                    func.count(Task.id).filter(Task.status == "done"),
                    func.count(Task.id).filter(
                        Task.due_date < today, Task.status.not_in(DONE_STATUSES)
                    ),
                    func.count(Task.id).filter(Task.status == "blocked"),
                ).where(*task_scope)
            ).one()
            tasks = TaskOverview(
                total=total, done=done, overdue=overdue, blocked=blocked
            )
            overdue_rows = db.scalars(
                select(Task)
                .where(
                    *task_scope,
                    Task.due_date < today,
                    Task.status.not_in(DONE_STATUSES),
                )
                .order_by(Task.due_date, Task.id)
                .limit(ATTENTION_LIMIT)
            ).all()
            overdue_ids = {task.id for task in overdue_rows}
            attention.extend(
                OverviewAttention(
                    kind="task_overdue",
                    code=task.task_code,
                    title=task.title,
                    state=f"{(today - task.due_date).days}日超過"
                    if task.due_date
                    else "期限超過",
                    target_id=str(task.id),
                )
                for task in overdue_rows
            )
            attention.extend(
                OverviewAttention(
                    kind="task_blocked",
                    code=task.task_code,
                    title=task.title,
                    state="ブロック中",
                    target_id=str(task.id),
                )
                for task in db.scalars(
                    select(Task)
                    .where(
                        *task_scope,
                        Task.status == "blocked",
                        Task.id.not_in(overdue_ids),
                    )
                    .order_by(Task.updated_at.desc(), Task.id.desc())
                    .limit(ATTENTION_LIMIT)
                )
            )

            issue_scope = (
                Task.project_id == project_id,
                Task.deleted_at.is_(None),
                Task.task_type == "bug",
            )
            issue_total, issue_open = db.execute(
                select(
                    func.count(Task.id),
                    func.count(Task.id).filter(Task.status.not_in(DONE_STATUSES)),
                ).where(*issue_scope)
            ).one()
            issues = IssueOverview(total=issue_total, open=issue_open)
        else:
            issue_scope = None

        if self._can(db, user, project_id, "test_case:read"):
            designs = self.designs.repository.list_designs(db, project_id)
            active_cases: list[TestCase] = []
            current_sources: dict[str, dict] = {}
            cases_by_design: dict[int, list[TestCase]] = defaultdict(list)
            for case in db.scalars(
                select(TestCase)
                .join(TestDesign, TestDesign.id == TestCase.design_id)
                .where(
                    TestDesign.project_id == project_id,
                    TestDesign.deleted_at.is_(None),
                )
            ):
                cases_by_design[case.design_id].append(case)
            for design in designs:
                sources = case_sources(self.designs.read(db, design))
                for case in cases_by_design[design.id]:
                    source = sources.get(case_key(case))
                    if source is not None:
                        active_cases.append(case)
                        current_sources[str(case.id)] = source
            counts = Counter(case.status for case in active_cases)
            failed = [case for case in active_cases if case.status == "failed"]
            linked_ids = set()
            if failed:
                linked_ids = set(
                    db.scalars(
                        select(TestCaseIssue.case_id)
                        .join(Task, Task.id == TestCaseIssue.task_id)
                        .where(
                            TestCaseIssue.case_id.in_([case.id for case in failed]),
                            Task.project_id == project_id,
                            Task.deleted_at.is_(None),
                        )
                    )
                )
            tests = TestOverview(
                total=len(active_cases),
                executed=counts["passed"] + counts["failed"],
                passed=counts["passed"],
                failed=counts["failed"],
                not_run=counts["not_run"],
                failed_without_issue=sum(case.id not in linked_ids for case in failed),
            )
            # Issue未登録NGを先に示し、同じケースをNGとして重複表示しない。
            failed.sort(
                key=lambda case: (case.id in linked_ids, case.design_id, case.position)
            )
            for case in failed[:ATTENTION_LIMIT]:
                item = current_sources[str(case.id)].get("item") or {}
                attention.append(
                    OverviewAttention(
                        kind="test_failed_no_issue"
                        if case.id not in linked_ids
                        else "test_failed",
                        code=str(item.get("code") or "テストケース"),
                        title=str(item.get("content") or "テストケース"),
                        state="Issue未登録のNG" if case.id not in linked_ids else "NG",
                        target_id=str(case.id),
                        design_id=case.design_id,
                    )
                )

        if issue_scope is not None:
            attention.extend(
                OverviewAttention(
                    kind="issue_open",
                    code=task.task_code,
                    title=task.title,
                    state="未解決",
                    target_id=str(task.id),
                )
                for task in db.scalars(
                    select(Task)
                    .where(*issue_scope, Task.status.not_in(DONE_STATUSES))
                    .order_by(
                        (Task.priority == "critical").desc(),
                        (Task.priority == "high").desc(),
                        Task.updated_at.desc(),
                    )
                    .limit(ATTENTION_LIMIT)
                ).all()
            )

        if self._can(db, user, project_id, "requirement:read"):
            coverage = self.collaboration.repository.coverage(db, project_id, None)
            uncovered_ids = [
                requirement_id for requirement_id, count in coverage if count == 0
            ]
            requirements = RequirementOverview(
                total=len(coverage),
                covered=len(coverage) - len(uncovered_ids),
                uncovered=len(uncovered_ids),
            )
            if uncovered_ids:
                attention.extend(
                    OverviewAttention(
                        kind="requirement_uncovered",
                        code=requirement.requirement_code,
                        title=requirement.title,
                        state="テスト未カバー",
                        target_id=str(requirement.id),
                        document_id=requirement.document_id,
                    )
                    for requirement in db.scalars(
                        select(Requirement)
                        .where(Requirement.id.in_(uncovered_ids))
                        .order_by(Requirement.id)
                        .limit(ATTENTION_LIMIT)
                    )
                )

        return ProjectOverviewRead(
            requirements=requirements,
            tasks=tasks,
            tests=tests,
            issues=issues,
            attention=attention[:ATTENTION_LIMIT],
            activities=self.activities(
                db, project_id, user, 1, ACTIVITY_PREVIEW_LIMIT
            ).items,
        )

    def activities(
        self, db: Session, project_id: int, user: User, page: int, page_size: int
    ) -> ProjectActivityListRead:
        """既存履歴を権限別に読み、日時順に統合する。"""
        ProjectService().get_project(db, project_id)
        fetch_limit = page * page_size + 1
        items: list[ProjectActivity] = []

        if self._can(db, user, project_id, "requirement:read"):
            requirement_rows = db.execute(
                select(RequirementChangeLog, RequirementDocument, Requirement)
                .join(
                    RequirementDocument,
                    RequirementDocument.id == RequirementChangeLog.document_id,
                )
                .outerjoin(
                    Requirement,
                    (Requirement.id == RequirementChangeLog.target_id)
                    & (RequirementChangeLog.target_type == "requirement_item")
                    & (Requirement.document_id == RequirementDocument.id),
                )
                .where(
                    RequirementDocument.project_id == project_id,
                    RequirementDocument.deleted_at.is_(None),
                    RequirementChangeLog.target_type.in_(
                        ("document", "requirement_item")
                    ),
                )
                .order_by(
                    RequirementChangeLog.changed_at.desc(),
                    RequirementChangeLog.id.desc(),
                )
                .limit(fetch_limit)
            ).all()
            items.extend(
                ProjectActivity(
                    kind="requirement",
                    action=log.action,
                    code=requirement.requirement_code
                    if requirement
                    else document.document_code,
                    title=requirement.title if requirement else document.title,
                    target_id=str(requirement.id if requirement else document.id),
                    document_id=document.id,
                    occurred_at=log.changed_at,
                    actor_name=str(log.changed_by) if log.changed_by else None,
                )
                for log, document, requirement in requirement_rows
            )

        if self._can(db, user, project_id, "task:read"):
            task_rows = db.execute(
                select(TaskChangeLog, Task)
                .join(Task, Task.id == TaskChangeLog.target_id)
                .where(
                    TaskChangeLog.project_id == project_id,
                    TaskChangeLog.target_type == "task",
                    Task.project_id == project_id,
                    Task.deleted_at.is_(None),
                )
                .order_by(TaskChangeLog.changed_at.desc(), TaskChangeLog.id.desc())
                .limit(fetch_limit)
            ).all()
            items.extend(
                ProjectActivity(
                    kind="issue" if task.task_type == "bug" else "task",
                    action=(
                        "completed"
                        if log.action == "status.changed"
                        and (log.new_value or {}).get("status") == "done"
                        else log.action
                    ),
                    code=task.task_code,
                    title=task.title,
                    target_id=str(task.id),
                    occurred_at=log.changed_at,
                    actor_name=str(log.changed_by) if log.changed_by else None,
                )
                for log, task in task_rows
            )

        if self._can(db, user, project_id, "test_plan:read"):
            design_rows = db.execute(
                select(AuditLog, TestDesign)
                .join(TestDesign, TestDesign.id == AuditLog.resource_id)
                .where(
                    AuditLog.project_id == project_id,
                    AuditLog.resource_type == "test_design",
                    AuditLog.event_type.in_(
                        (
                            "test_design.created",
                            "test_design.updated",
                        )
                    ),
                    TestDesign.project_id == project_id,
                    TestDesign.deleted_at.is_(None),
                )
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .limit(fetch_limit)
            ).all()
            items.extend(
                ProjectActivity(
                    kind="test_design",
                    action=log.event_type.removeprefix("test_design."),
                    code=design.name,
                    title=design.name,
                    target_id=str(design.id),
                    design_id=design.id,
                    occurred_at=log.created_at,
                    actor_name=str(log.actor_user_id) if log.actor_user_id else None,
                )
                for log, design in design_rows
            )

        if self._can(db, user, project_id, "test_case:read"):
            execution_rows = db.execute(
                select(TestExecution, TestCase)
                .join(TestCase, TestCase.id == TestExecution.case_id)
                .join(TestDesign, TestDesign.id == TestCase.design_id)
                .where(
                    TestDesign.project_id == project_id, TestDesign.deleted_at.is_(None)
                )
                .order_by(TestExecution.executed_at.desc(), TestExecution.id.desc())
                .limit(fetch_limit)
            ).all()
            items.extend(
                ProjectActivity(
                    kind="test_execution",
                    action=execution.status,
                    code=str(
                        (execution.source.get("item") or {}).get("code")
                        or "テストケース"
                    ),
                    title=str(
                        (execution.source.get("item") or {}).get("content")
                        or "テスト実行"
                    ),
                    target_id=str(case.id),
                    design_id=case.design_id,
                    occurred_at=execution.executed_at,
                    actor_name=str(execution.executed_by)
                    if execution.executed_by
                    else None,
                )
                for execution, case in execution_rows
            )

        actor_ids = {int(item.actor_name) for item in items if item.actor_name}
        names = (
            {
                user_id: name
                for user_id, name in db.execute(
                    select(User.id, User.name).where(User.id.in_(actor_ids))
                )
            }
            if actor_ids
            else {}
        )
        for item in items:
            if item.actor_name:
                item.actor_name = names.get(int(item.actor_name), "不明なユーザー")
        items.sort(
            key=lambda item: (
                item.occurred_at.replace(tzinfo=timezone.utc)
                if item.occurred_at.tzinfo is None
                else item.occurred_at
            ),
            reverse=True,
        )
        start = (page - 1) * page_size
        return ProjectActivityListRead(
            items=items[start : start + page_size],
            page=page,
            page_size=page_size,
            has_more=len(items) > start + page_size,
        )
