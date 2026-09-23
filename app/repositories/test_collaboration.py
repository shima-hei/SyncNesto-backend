"""テスト設計の関連、議論、実行証跡のDBアクセス。"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.requirement import Requirement, RequirementDocument
from app.models.test_design import (
    RequirementTestItem,
    TestCase,
    TestDesign,
    TestDesignComment,
    TestDesignCommentChange,
    TestEvidence,
    TestExecution,
    TestItem,
)
from app.models.user import User


class TestCollaborationRepository:
    """独立した関連と履歴の読み取りをまとめる。"""

    def item(self, db: Session, design_id: int, item_id: UUID) -> TestItem | None:
        """設計書内の項目を削除済みも含めて取得する。"""
        return db.scalar(
            select(TestItem).where(
                TestItem.design_id == design_id, TestItem.id == item_id
            )
        )

    def requirement(
        self, db: Session, project_id: int, requirement_id: int
    ) -> Requirement | None:
        """同一プロジェクトの要件を取得する。"""
        return db.scalar(
            select(Requirement)
            .join(
                RequirementDocument, RequirementDocument.id == Requirement.document_id
            )
            .where(
                Requirement.id == requirement_id,
                RequirementDocument.project_id == project_id,
                Requirement.deleted_at.is_(None),
                RequirementDocument.deleted_at.is_(None),
            )
        )

    def link(self, db: Session, link_id: int) -> RequirementTestItem | None:
        """関連を取得する。"""
        return db.get(RequirementTestItem, link_id)

    def link_by_pair(
        self, db: Session, requirement_id: int, item_id: UUID
    ) -> RequirementTestItem | None:
        """重複関連を取得する。"""
        return db.scalar(
            select(RequirementTestItem).where(
                RequirementTestItem.requirement_id == requirement_id,
                RequirementTestItem.item_id == item_id,
            )
        )

    def links_for_design(self, db: Session, design_id: int) -> list[tuple]:
        """項目側に表示する関連と要件情報を取得する。"""
        return [
            tuple(row)
            for row in db.execute(
                select(RequirementTestItem, Requirement, TestItem, TestDesign)
                .join(Requirement, Requirement.id == RequirementTestItem.requirement_id)
                .join(TestItem, TestItem.id == RequirementTestItem.item_id)
                .join(TestDesign, TestDesign.id == TestItem.design_id)
                .where(
                    TestItem.design_id == design_id, Requirement.deleted_at.is_(None)
                )
                .order_by(TestItem.position, Requirement.requirement_code)
            )
        ]

    def links_for_requirement(self, db: Session, requirement_id: int) -> list[tuple]:
        """要件側に表示する関連と項目情報を取得する。"""
        return [
            tuple(row)
            for row in db.execute(
                select(RequirementTestItem, Requirement, TestItem, TestDesign)
                .join(Requirement, Requirement.id == RequirementTestItem.requirement_id)
                .join(TestItem, TestItem.id == RequirementTestItem.item_id)
                .join(TestDesign, TestDesign.id == TestItem.design_id)
                .where(RequirementTestItem.requirement_id == requirement_id)
                .order_by(TestDesign.name, TestItem.position)
            )
        ]

    def coverage(
        self, db: Session, project_id: int, document_id: int | None
    ) -> list[tuple[int, int]]:
        """有効な関連項目数を要件ごとに集計する。"""
        query = (
            select(Requirement.id, func.count(TestDesign.id))
            .join(
                RequirementDocument, RequirementDocument.id == Requirement.document_id
            )
            .outerjoin(
                RequirementTestItem,
                RequirementTestItem.requirement_id == Requirement.id,
            )
            .outerjoin(
                TestItem,
                (TestItem.id == RequirementTestItem.item_id)
                & TestItem.deleted_at.is_(None)
                & TestItem.is_spacer.is_(False),
            )
            .outerjoin(
                TestDesign,
                (TestDesign.id == TestItem.design_id) & TestDesign.deleted_at.is_(None),
            )
            .where(
                RequirementDocument.project_id == project_id,
                RequirementDocument.deleted_at.is_(None),
                Requirement.deleted_at.is_(None),
            )
            .group_by(Requirement.id)
        )
        if document_id is not None:
            query = query.where(Requirement.document_id == document_id)
        return [(row[0], row[1]) for row in db.execute(query)]

    def comments(self, db: Session, design_id: int) -> list[TestDesignComment]:
        """削除済み親コメントも含め、スレッドを順に取得する。"""
        return list(
            db.scalars(
                select(TestDesignComment)
                .where(TestDesignComment.design_id == design_id)
                .order_by(TestDesignComment.created_at, TestDesignComment.id)
            )
        )

    def comment(
        self, db: Session, design_id: int, comment_id: int
    ) -> TestDesignComment | None:
        """設計書内のコメントを取得する。"""
        return db.scalar(
            select(TestDesignComment).where(
                TestDesignComment.design_id == design_id,
                TestDesignComment.id == comment_id,
            )
        )

    def comment_changes(
        self, db: Session, comment_id: int
    ) -> list[TestDesignCommentChange]:
        """コメントの変更履歴を時系列で取得する。"""
        return list(
            db.scalars(
                select(TestDesignCommentChange)
                .where(TestDesignCommentChange.comment_id == comment_id)
                .order_by(TestDesignCommentChange.created_at)
            )
        )

    def user_names(self, db: Session, user_ids: set[int]) -> dict[int, str]:
        """投稿者・実行者の表示名を一括取得する。"""
        return (
            {
                row.id: row.name
                for row in db.scalars(select(User).where(User.id.in_(user_ids)))
            }
            if user_ids
            else {}
        )

    def case(self, db: Session, design_id: int, case_id: UUID) -> TestCase | None:
        """設計書内のケースを取得する。"""
        return db.scalar(
            select(TestCase).where(
                TestCase.design_id == design_id, TestCase.id == case_id
            )
        )

    def executions(self, db: Session, case_id: UUID) -> list[TestExecution]:
        """新しい順に実行履歴を取得する。"""
        return list(
            db.scalars(
                select(TestExecution)
                .where(TestExecution.case_id == case_id)
                .order_by(TestExecution.run_number.desc())
            )
        )

    def execution(
        self, db: Session, case_id: UUID, execution_id: UUID
    ) -> TestExecution | None:
        """ケース内の実行を取得する。"""
        return db.scalar(
            select(TestExecution).where(
                TestExecution.case_id == case_id, TestExecution.id == execution_id
            )
        )

    def evidence(self, db: Session, execution_id: UUID) -> list[TestEvidence]:
        """実行の有効な証跡を取得する。"""
        return list(
            db.scalars(
                select(TestEvidence)
                .where(
                    TestEvidence.execution_id == execution_id,
                    TestEvidence.deleted_at.is_(None),
                )
                .order_by(TestEvidence.uploaded_at)
            )
        )

    def evidence_by_id(
        self, db: Session, execution_id: UUID, evidence_id: UUID
    ) -> TestEvidence | None:
        """実行内の有効な証跡を取得する。"""
        return db.scalar(
            select(TestEvidence).where(
                TestEvidence.execution_id == execution_id,
                TestEvidence.id == evidence_id,
                TestEvidence.deleted_at.is_(None),
            )
        )
