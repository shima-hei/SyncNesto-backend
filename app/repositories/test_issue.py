"""ケースと不具合タスクの関連を読み書きする。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.task import Task
from app.models.test_design import TestCase, TestCaseIssue, TestDesign


class TestIssueRepository:
    """ケース、タスク、関連のDBアクセス。"""

    def task(self, db: Session, project_id: int, task_id: int) -> Task | None:
        """同一プロジェクトの有効な不具合タスクだけを取得する。"""
        return db.scalar(
            select(Task).where(
                Task.id == task_id,
                Task.project_id == project_id,
                Task.task_type == "bug",
                Task.deleted_at.is_(None),
            )
        )

    def pair(self, db: Session, case_id: UUID, task_id: int) -> TestCaseIssue | None:
        """同一組の既存関連を取得する。"""
        return db.scalar(
            select(TestCaseIssue).where(
                TestCaseIssue.case_id == case_id, TestCaseIssue.task_id == task_id
            )
        )

    def link(self, db: Session, case_id: UUID, link_id: int) -> TestCaseIssue | None:
        """ケースに属する関連を取得する。"""
        return db.scalar(
            select(TestCaseIssue).where(
                TestCaseIssue.case_id == case_id, TestCaseIssue.id == link_id
            )
        )

    def for_design(
        self, db: Session, project_id: int, design_id: int
    ) -> list[tuple[TestCaseIssue, Task, TestCase]]:
        """設計書内の有効な不具合関連をまとめて取得する。"""
        return [
            (link, task, case)
            for link, task, case in db.execute(
                select(TestCaseIssue, Task, TestCase)
                .join(Task, Task.id == TestCaseIssue.task_id)
                .join(TestCase, TestCase.id == TestCaseIssue.case_id)
                .where(
                    TestCase.design_id == design_id,
                    Task.project_id == project_id,
                    Task.task_type == "bug",
                    Task.deleted_at.is_(None),
                )
                .order_by(Task.task_code, TestCase.position)
            )
        ]

    def for_case(
        self, db: Session, project_id: int, case_id: UUID
    ) -> list[tuple[TestCaseIssue, Task]]:
        """一件のケースに関連する有効な不具合だけを取得する。"""
        return [
            (link, task)
            for link, task in db.execute(
                select(TestCaseIssue, Task)
                .join(Task, Task.id == TestCaseIssue.task_id)
                .where(
                    TestCaseIssue.case_id == case_id,
                    Task.project_id == project_id,
                    Task.task_type == "bug",
                    Task.deleted_at.is_(None),
                )
                .order_by(Task.task_code)
            )
        ]

    def for_task(
        self, db: Session, project_id: int, task_id: int
    ) -> list[tuple[TestCaseIssue, TestCase, TestDesign]]:
        """不具合タスクから同一プロジェクト内のケースを取得する。"""
        return [
            (link, case, design)
            for link, case, design in db.execute(
                select(TestCaseIssue, TestCase, TestDesign)
                .join(TestCase, TestCase.id == TestCaseIssue.case_id)
                .join(TestDesign, TestDesign.id == TestCase.design_id)
                .where(
                    TestCaseIssue.task_id == task_id,
                    TestDesign.project_id == project_id,
                    TestDesign.deleted_at.is_(None),
                )
                .order_by(TestDesign.name, TestCase.position)
            )
        ]
