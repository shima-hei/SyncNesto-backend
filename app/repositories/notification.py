"""宛先で制限した通知DBアクセスと対象の一括存在確認。"""

from datetime import UTC, datetime

from sqlalchemy import exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.notification import NotificationTargetType as Target
from app.models.project import Project
from app.models.requirement import (
    Requirement,
    RequirementComment,
    RequirementDocument,
    RequirementOpenIssue,
    RequirementSection,
    RequirementTargetComment,
)
from app.models.task import Task, TaskComment
from app.models.test_design import (
    ExpectedValue,
    Factor,
    FactorLevel,
    PatternTable,
    TestDesign,
    TestDesignComment,
    TestItem,
    TestPattern,
)


class NotificationRepository:
    """通知生成はcommitせず、業務更新のトランザクションへ参加する。"""

    def create_once(self, db: Session, **values) -> None:
        """同一イベント・宛先の再評価をDBの一意制約で無害化する。"""
        values["tenant_id"] = db.info["tenant_id"]
        db.execute(
            insert(Notification)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_notification_event_recipient")
        )

    def query(self, db: Session, recipient_id: int, project_id: int | None = None):
        """宛先本人の通知だけに制限する。"""
        query = db.query(Notification).filter(
            Notification.recipient_user_id == recipient_id
        )
        if project_id is not None:
            query = query.filter(Notification.project_id == project_id)
        return query

    def list_paginated(
        self,
        db: Session,
        recipient_id: int,
        *,
        page: int,
        page_size: int,
        unread_only: bool,
        project_id: int | None,
    ):
        """作成日時とIDの順で、最大100件だけ取得する。"""
        query = self.query(db, recipient_id, project_id)
        if unread_only:
            query = query.filter(Notification.read_at.is_(None))
        total = query.count()
        items = (
            query.order_by(Notification.created_at.desc(), Notification.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total

    def unread_count(
        self, db: Session, recipient_id: int, project_id: int | None
    ) -> int:
        """未読件数を集計する。"""
        return (
            self.query(db, recipient_id, project_id)
            .filter(Notification.read_at.is_(None))
            .count()
        )

    def get(
        self, db: Session, recipient_id: int, notification_id: int
    ) -> Notification | None:
        """他人の通知IDは存在しないものとして扱う。"""
        return (
            self.query(db, recipient_id)
            .filter(Notification.id == notification_id)
            .first()
        )

    def mark_read(self, db: Session, recipient_id: int, notification_id: int) -> None:
        """既読日時を初回だけ保存する。"""
        db.execute(
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.recipient_user_id == recipient_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=datetime.now(UTC))
        )
        db.commit()

    def mark_all_read(
        self, db: Session, recipient_id: int, project_id: int | None
    ) -> int:
        """一つのUPDATEで指定範囲の未読通知を既読化する。"""
        query = self.query(db, recipient_id, project_id).filter(
            Notification.read_at.is_(None)
        )
        count = query.update(
            {Notification.read_at: datetime.now(UTC)}, synchronize_session=False
        )
        db.commit()
        return count

    def available_targets(
        self, db: Session, items: list[Notification]
    ) -> set[tuple[str, str]]:
        """件数によるN+1を避け、対象種別ごとに親の削除も確認する。"""
        queries = {
            Target.TASK: select(Task.id)
            .join(Project)
            .where(Task.deleted_at.is_(None), Project.deleted_at.is_(None)),
            Target.REQUIREMENT: select(Requirement.id)
            .join(RequirementDocument)
            .join(Project)
            .where(
                Requirement.deleted_at.is_(None),
                RequirementDocument.deleted_at.is_(None),
                Project.deleted_at.is_(None),
            ),
            Target.OPEN_ISSUE: select(RequirementOpenIssue.id)
            .join(RequirementDocument)
            .join(Project)
            .where(
                RequirementOpenIssue.deleted_at.is_(None),
                RequirementDocument.deleted_at.is_(None),
                Project.deleted_at.is_(None),
            ),
            Target.TASK_COMMENT: select(TaskComment.id)
            .join(Task)
            .join(Project)
            .where(
                TaskComment.deleted_at.is_(None),
                Task.deleted_at.is_(None),
                Project.deleted_at.is_(None),
            ),
            Target.REQUIREMENT_COMMENT: select(RequirementComment.id)
            .join(Requirement)
            .join(RequirementDocument)
            .join(Project)
            .where(
                Requirement.deleted_at.is_(None),
                RequirementDocument.deleted_at.is_(None),
                Project.deleted_at.is_(None),
            ),
            Target.REQUIREMENT_TARGET_COMMENT: select(RequirementTargetComment.id)
            .join(RequirementDocument)
            .join(Project)
            .where(
                RequirementTargetComment.deleted_at.is_(None),
                RequirementDocument.deleted_at.is_(None),
                Project.deleted_at.is_(None),
            ),
            Target.TEST_DESIGN_COMMENT: select(TestDesignComment.id)
            .join(TestDesign)
            .join(Project)
            .where(
                TestDesignComment.deleted_at.is_(None),
                TestDesign.deleted_at.is_(None),
                Project.deleted_at.is_(None),
            ),
        }
        models = {
            Target.TASK: Task,
            Target.REQUIREMENT: Requirement,
            Target.OPEN_ISSUE: RequirementOpenIssue,
            Target.TASK_COMMENT: TaskComment,
            Target.REQUIREMENT_COMMENT: RequirementComment,
            Target.REQUIREMENT_TARGET_COMMENT: RequirementTargetComment,
            Target.TEST_DESIGN_COMMENT: TestDesignComment,
        }
        subject_checks = []
        for kind, model in {
            "requirement_item": Requirement,
            "section": RequirementSection,
            "open_issue": RequirementOpenIssue,
        }.items():
            subject_checks.append(
                (RequirementTargetComment.target_type == kind)
                & exists().where(
                    model.id == RequirementTargetComment.target_id,
                    model.deleted_at.is_(None),
                )
            )
        queries[Target.REQUIREMENT_TARGET_COMMENT] = queries[
            Target.REQUIREMENT_TARGET_COMMENT
        ].where(
            or_(RequirementTargetComment.target_type == "document", *subject_checks)
        )
        design_checks = []
        for kind, model in {
            "test_item": TestItem,
            "pattern_table": PatternTable,
            "factor": Factor,
            "factor_level": FactorLevel,
            "combination": TestPattern,
            "expected_value": ExpectedValue,
        }.items():
            design_checks.append(
                (TestDesignComment.target_type == kind)
                & exists().where(
                    model.id == TestDesignComment.target_id, model.deleted_at.is_(None)
                )
            )
        queries[Target.TEST_DESIGN_COMMENT] = queries[Target.TEST_DESIGN_COMMENT].where(
            or_(TestDesignComment.target_type == "design", *design_checks)
        )
        available = set()
        for kind, model in models.items():
            ids = [int(item.target_id) for item in items if item.target_type == kind]
            if ids:
                available.update(
                    (kind.value, str(identifier))
                    for identifier in db.scalars(queries[kind].where(model.id.in_(ids)))
                )
        return available
