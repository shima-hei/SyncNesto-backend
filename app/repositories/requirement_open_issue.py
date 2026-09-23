"""要件定義Repositoryを定義するモジュール。"""

from datetime import UTC, date, datetime

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.models.requirement import (
    RequirementOpenIssue,
)
from app.schemas.requirement import (
    RequirementOpenIssueCreate,
    RequirementOpenIssueUpdate,
)


class RequirementOpenIssueRepository:
    """RequirementOpenIssueテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        issue_in: RequirementOpenIssueCreate,
        actor_id: int | None = None,
    ) -> RequirementOpenIssue:
        """未決事項を作成する。"""
        issue = RequirementOpenIssue(
            document_id=issue_in.document_id,
            related_requirement_id=issue_in.related_requirement_id,
            issue_code=issue_in.issue_code,
            title=issue_in.title,
            description=issue_in.description,
            impact_scope=issue_in.impact_scope,
            assignee_id=issue_in.assignee_id,
            due_date=issue_in.due_date,
            status=issue_in.status,
            resolution=issue_in.resolution,
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(issue)
        db.commit()
        db.refresh(issue)
        return issue

    def get_by_id(self, db: Session, issue_id: int) -> RequirementOpenIssue | None:
        """idに一致する未決事項を取得する。"""
        return (
            db.query(RequirementOpenIssue)
            .filter(
                RequirementOpenIssue.id == issue_id,
                RequirementOpenIssue.deleted_at.is_(None),
            )
            .first()
        )

    def list_by_ids(
        self,
        db: Session,
        issue_ids: list[int],
    ) -> list[RequirementOpenIssue]:
        """id一覧に一致する未決事項一覧を取得する。"""
        if not issue_ids:
            return []

        return (
            db.query(RequirementOpenIssue)
            .filter(
                RequirementOpenIssue.id.in_(issue_ids),
                RequirementOpenIssue.deleted_at.is_(None),
            )
            .order_by(RequirementOpenIssue.id)
            .all()
        )

    def get_by_document_issue_code(
        self,
        db: Session,
        *,
        document_id: int,
        issue_code: str,
    ) -> RequirementOpenIssue | None:
        """document_id/issue_codeに一致する未決事項を取得する。"""
        return (
            db.query(RequirementOpenIssue)
            .filter(
                RequirementOpenIssue.document_id == document_id,
                RequirementOpenIssue.issue_code == issue_code,
                RequirementOpenIssue.deleted_at.is_(None),
            )
            .first()
        )

    def get_max_auto_issue_number(self, db: Session, document_id: int) -> int:
        """要件定義書内の自動採番未決事項ID最大番号を取得する。

        Args:
            db: DBセッション。
            document_id: 採番対象の要件定義書ID。

        Returns:
            `ISSUE-001` 形式の最大番号。存在しない場合は0。
        """
        result = db.execute(
            text(
                """
                SELECT COALESCE(
                    MAX(CAST(substring(issue_code from '^ISSUE-(\\d+)$') AS INTEGER)),
                    0
                )
                FROM requirement_open_issues
                WHERE document_id = :document_id
                  AND issue_code ~ '^ISSUE-[0-9]+$'
                """
            ),
            {"document_id": document_id},
        ).scalar_one()
        return int(result)

    def list_paginated(
        self,
        db: Session,
        *,
        document_ids: list[int],
        page: int,
        page_size: int,
        q: str | None = None,
        status: str | None = None,
        assignee_id: int | None = None,
        due_date_from: date | None = None,
        due_date_to: date | None = None,
        related_requirement_id: int | None = None,
    ) -> tuple[list[RequirementOpenIssue], int]:
        """指定要件定義書群の未決事項一覧をページング付きで取得する。"""
        query = db.query(RequirementOpenIssue).filter(
            RequirementOpenIssue.document_id.in_(document_ids),
            RequirementOpenIssue.deleted_at.is_(None),
        )
        if q:
            like_pattern = f"%{q}%"
            query = query.filter(
                or_(
                    RequirementOpenIssue.issue_code.ilike(like_pattern),
                    RequirementOpenIssue.title.ilike(like_pattern),
                    RequirementOpenIssue.description.ilike(like_pattern),
                )
            )
        if status is not None:
            query = query.filter(RequirementOpenIssue.status == status)
        if assignee_id is not None:
            query = query.filter(RequirementOpenIssue.assignee_id == assignee_id)
        if due_date_from is not None:
            query = query.filter(RequirementOpenIssue.due_date >= due_date_from)
        if due_date_to is not None:
            query = query.filter(RequirementOpenIssue.due_date <= due_date_to)
        if related_requirement_id is not None:
            query = query.filter(
                RequirementOpenIssue.related_requirement_id == related_requirement_id
            )

        total = query.count()
        issues = (
            query.order_by(RequirementOpenIssue.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return issues, total

    def list_by_document(
        self,
        db: Session,
        document_id: int,
    ) -> list[RequirementOpenIssue]:
        """指定要件定義書の未決事項一覧を取得する。"""
        return (
            db.query(RequirementOpenIssue)
            .filter(
                RequirementOpenIssue.document_id == document_id,
                RequirementOpenIssue.deleted_at.is_(None),
            )
            .order_by(RequirementOpenIssue.id)
            .all()
        )

    def update(
        self,
        db: Session,
        *,
        issue: RequirementOpenIssue,
        issue_in: RequirementOpenIssueUpdate,
        actor_id: int | None = None,
    ) -> RequirementOpenIssue:
        """未決事項を更新する。"""
        for field in (
            "related_requirement_id",
            "title",
            "description",
            "impact_scope",
            "assignee_id",
            "due_date",
            "status",
            "resolution",
        ):
            if field in issue_in.model_fields_set:
                setattr(issue, field, getattr(issue_in, field))
        if actor_id is not None:
            issue.updated_by = actor_id
        issue.version += 1

        db.commit()
        db.refresh(issue)
        return issue

    def mark_promoted(
        self,
        db: Session,
        *,
        issue: RequirementOpenIssue,
        requirement_id: int,
        resolution: str | None,
        actor_id: int | None = None,
    ) -> RequirementOpenIssue:
        """未決事項を要件へ昇格済みにする。"""
        issue.related_requirement_id = requirement_id
        issue.status = "resolved"
        if resolution is not None:
            issue.resolution = resolution
        if actor_id is not None:
            issue.updated_by = actor_id
        issue.version += 1

        db.commit()
        db.refresh(issue)
        return issue

    def soft_delete(
        self,
        db: Session,
        *,
        issue: RequirementOpenIssue,
        actor_id: int | None = None,
    ) -> RequirementOpenIssue:
        """未決事項を論理削除する。"""
        issue.deleted_at = datetime.now(UTC)
        if actor_id is not None:
            issue.updated_by = actor_id
        db.commit()
        db.refresh(issue)
        return issue
