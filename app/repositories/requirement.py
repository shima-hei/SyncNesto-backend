"""要件定義Repositoryを定義するモジュール。"""

from datetime import UTC, datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.requirement import (
    Requirement,
    RequirementChangeLog,
    RequirementComment,
    RequirementDetail,
    RequirementDocument,
    RequirementLink,
    RequirementOpenIssue,
    RequirementReview,
    RequirementRevision,
    RequirementSection,
    RequirementTargetComment,
)
from app.schemas.requirement import (
    RequirementCommentCreate,
    RequirementCreate,
    RequirementDetailCreate,
    RequirementDetailUpdate,
    RequirementDocumentCreate,
    RequirementDocumentUpdate,
    RequirementLinkCreate,
    RequirementOpenIssueCreate,
    RequirementOpenIssueUpdate,
    RequirementReviewCreate,
    RequirementReviewUpdate,
    RequirementSectionCreate,
    RequirementSectionUpdate,
    RequirementTargetCommentCreate,
    RequirementTargetCommentUpdate,
    RequirementUpdate,
)


class RequirementDocumentRepository:
    """RequirementDocumentテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        project_id: int,
        document_in: RequirementDocumentCreate,
        actor_id: int | None = None,
    ) -> RequirementDocument:
        """要件定義書を作成する。"""
        document = RequirementDocument(
            project_id=project_id,
            title=document_in.title,
            document_code=document_in.document_code,
            status=document_in.status,
            purpose=document_in.purpose,
            target_system_name=document_in.target_system_name,
            client_name=document_in.client_name,
            vendor_name=document_in.vendor_name,
            author_id=document_in.author_id,
            reviewer_id=document_in.reviewer_id,
            approver_id=document_in.approver_id,
            approved_at=document_in.approved_at,
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        return document

    def get_by_id(
        self,
        db: Session,
        document_id: int,
    ) -> RequirementDocument | None:
        """idに一致する要件定義書を取得する。"""
        return (
            db.query(RequirementDocument)
            .filter(
                RequirementDocument.id == document_id,
                RequirementDocument.deleted_at.is_(None),
            )
            .first()
        )

    def get_by_project_document_code(
        self,
        db: Session,
        *,
        project_id: int,
        document_code: str,
    ) -> RequirementDocument | None:
        """project_id/document_codeに一致する要件定義書を取得する。"""
        return (
            db.query(RequirementDocument)
            .filter(
                RequirementDocument.project_id == project_id,
                RequirementDocument.document_code == document_code,
                RequirementDocument.deleted_at.is_(None),
            )
            .first()
        )

    def list_paginated(
        self,
        db: Session,
        *,
        project_id: int,
        page: int,
        page_size: int,
        q: str | None = None,
        status: str | None = None,
    ) -> tuple[list[RequirementDocument], int]:
        """プロジェクト内の要件定義書一覧をページング付きで取得する。"""
        query = db.query(RequirementDocument).filter(
            RequirementDocument.project_id == project_id,
            RequirementDocument.deleted_at.is_(None),
        )
        if q:
            like_pattern = f"%{q}%"
            query = query.filter(
                or_(
                    RequirementDocument.document_code.ilike(like_pattern),
                    RequirementDocument.title.ilike(like_pattern),
                    RequirementDocument.purpose.ilike(like_pattern),
                )
            )
        if status is not None:
            query = query.filter(RequirementDocument.status == status)

        total = query.count()
        documents = (
            query.order_by(RequirementDocument.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return documents, total

    def list_ids_by_project(self, db: Session, project_id: int) -> list[int]:
        """プロジェクト内の要件定義書ID一覧を取得する。"""
        rows = (
            db.query(RequirementDocument.id)
            .filter(
                RequirementDocument.project_id == project_id,
                RequirementDocument.deleted_at.is_(None),
            )
            .order_by(RequirementDocument.id)
            .all()
        )
        return [row[0] for row in rows]

    def update(
        self,
        db: Session,
        *,
        document: RequirementDocument,
        document_in: RequirementDocumentUpdate,
        actor_id: int | None = None,
    ) -> RequirementDocument:
        """要件定義書を更新する。"""
        for field in (
            "title",
            "document_code",
            "status",
            "purpose",
            "target_system_name",
            "client_name",
            "vendor_name",
            "author_id",
            "reviewer_id",
            "approver_id",
            "approved_at",
        ):
            if field in document_in.model_fields_set:
                setattr(document, field, getattr(document_in, field))
        if actor_id is not None:
            document.updated_by = actor_id
        document.version += 1

        db.commit()
        db.refresh(document)
        return document

    def soft_delete(
        self,
        db: Session,
        *,
        document: RequirementDocument,
        actor_id: int | None = None,
    ) -> RequirementDocument:
        """要件定義書を論理削除する。"""
        document.deleted_at = datetime.now(UTC)
        if actor_id is not None:
            document.updated_by = actor_id
        db.commit()
        db.refresh(document)
        return document


class RequirementSectionRepository:
    """RequirementSectionテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        document_id: int,
        section_in: RequirementSectionCreate,
        actor_id: int | None = None,
    ) -> RequirementSection:
        """要件定義セクションを作成する。"""
        section = RequirementSection(
            document_id=document_id,
            title=section_in.title,
            section_type=section_in.section_type,
            content=section_in.content,
            sort_order=section_in.sort_order,
            status=section_in.status,
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(section)
        db.commit()
        db.refresh(section)
        return section

    def get_by_id(self, db: Session, section_id: int) -> RequirementSection | None:
        """idに一致する要件定義セクションを取得する。"""
        return (
            db.query(RequirementSection)
            .filter(
                RequirementSection.id == section_id,
                RequirementSection.deleted_at.is_(None),
            )
            .first()
        )

    def list_by_document(
        self,
        db: Session,
        document_id: int,
    ) -> list[RequirementSection]:
        """指定要件定義書のセクション一覧を取得する。"""
        return (
            db.query(RequirementSection)
            .filter(
                RequirementSection.document_id == document_id,
                RequirementSection.deleted_at.is_(None),
            )
            .order_by(RequirementSection.sort_order, RequirementSection.id)
            .all()
        )

    def update(
        self,
        db: Session,
        *,
        section: RequirementSection,
        section_in: RequirementSectionUpdate,
        actor_id: int | None = None,
    ) -> RequirementSection:
        """要件定義セクションを更新する。"""
        for field in ("title", "section_type", "content", "sort_order", "status"):
            if field in section_in.model_fields_set:
                setattr(section, field, getattr(section_in, field))
        if actor_id is not None:
            section.updated_by = actor_id
        section.version += 1

        db.commit()
        db.refresh(section)
        return section

    def update_sort_orders(
        self,
        db: Session,
        *,
        sections: list[RequirementSection],
        sort_orders_by_id: dict[int, int],
        actor_id: int | None = None,
    ) -> list[RequirementSection]:
        """要件定義セクションの表示順をまとめて更新する。"""
        for section in sections:
            section.sort_order = sort_orders_by_id[section.id]
            if actor_id is not None:
                section.updated_by = actor_id
            section.version += 1

        db.commit()
        for section in sections:
            db.refresh(section)
        return sorted(sections, key=lambda item: (item.sort_order, item.id))

    def soft_delete(
        self,
        db: Session,
        *,
        section: RequirementSection,
        actor_id: int | None = None,
    ) -> RequirementSection:
        """要件定義セクションを論理削除する。"""
        section.deleted_at = datetime.now(UTC)
        if actor_id is not None:
            section.updated_by = actor_id
        db.commit()
        db.refresh(section)
        return section


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
            "issue_code",
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


class RequirementChangeLogRepository:
    """RequirementChangeLogテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        target_type: str,
        target_id: int,
        action: str,
        document_id: int | None = None,
        field_name: str | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        reason: str | None = None,
        changed_by: int | None = None,
    ) -> RequirementChangeLog:
        """要件定義変更履歴を作成する。"""
        change_log = RequirementChangeLog(
            document_id=document_id,
            target_type=target_type,
            target_id=target_id,
            action=action,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            changed_by=changed_by,
        )
        db.add(change_log)
        db.commit()
        db.refresh(change_log)
        return change_log

    def list_paginated(
        self,
        db: Session,
        *,
        page: int,
        page_size: int,
        document_ids: list[int] | None = None,
        document_id: int | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        action: str | None = None,
    ) -> tuple[list[RequirementChangeLog], int]:
        """要件定義変更履歴一覧をページング付きで取得する。"""
        query = db.query(RequirementChangeLog)
        if document_ids is not None:
            query = query.filter(RequirementChangeLog.document_id.in_(document_ids))
        if document_id is not None:
            query = query.filter(RequirementChangeLog.document_id == document_id)
        if target_type is not None:
            query = query.filter(RequirementChangeLog.target_type == target_type)
        if target_id is not None:
            query = query.filter(RequirementChangeLog.target_id == target_id)
        if action is not None:
            query = query.filter(RequirementChangeLog.action == action)

        total = query.count()
        change_logs = (
            query.order_by(RequirementChangeLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return change_logs, total

    def list_by_document(
        self,
        db: Session,
        document_id: int,
    ) -> list[RequirementChangeLog]:
        """指定要件定義書の変更履歴一覧を取得する。"""
        return (
            db.query(RequirementChangeLog)
            .filter(RequirementChangeLog.document_id == document_id)
            .order_by(RequirementChangeLog.id)
            .all()
        )


class RequirementTargetCommentRepository:
    """RequirementTargetCommentテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        document_id: int,
        comment_in: RequirementTargetCommentCreate,
        author_id: int,
    ) -> RequirementTargetComment:
        """要件定義対象コメントを作成する。"""
        comment = RequirementTargetComment(
            document_id=document_id,
            target_type=comment_in.target_type,
            target_id=comment_in.target_id,
            parent_comment_id=comment_in.parent_comment_id,
            body=comment_in.body,
            author_id=author_id,
        )
        db.add(comment)
        db.commit()
        db.refresh(comment)
        return comment

    def get_by_id(
        self,
        db: Session,
        comment_id: int,
    ) -> RequirementTargetComment | None:
        """idに一致する要件定義対象コメントを取得する。"""
        return (
            db.query(RequirementTargetComment)
            .filter(
                RequirementTargetComment.id == comment_id,
                RequirementTargetComment.deleted_at.is_(None),
            )
            .first()
        )

    def list_by_target(
        self,
        db: Session,
        *,
        document_id: int,
        target_type: str,
        target_id: int,
    ) -> list[RequirementTargetComment]:
        """対象に紐づくコメント一覧を取得する。"""
        return (
            db.query(RequirementTargetComment)
            .filter(
                RequirementTargetComment.document_id == document_id,
                RequirementTargetComment.target_type == target_type,
                RequirementTargetComment.target_id == target_id,
                RequirementTargetComment.deleted_at.is_(None),
            )
            .order_by(RequirementTargetComment.id)
            .all()
        )

    def list_by_document(
        self,
        db: Session,
        document_id: int,
    ) -> list[RequirementTargetComment]:
        """指定要件定義書のコメント一覧を取得する。"""
        return (
            db.query(RequirementTargetComment)
            .filter(
                RequirementTargetComment.document_id == document_id,
                RequirementTargetComment.deleted_at.is_(None),
            )
            .order_by(RequirementTargetComment.id)
            .all()
        )

    def update(
        self,
        db: Session,
        *,
        comment: RequirementTargetComment,
        comment_in: RequirementTargetCommentUpdate,
    ) -> RequirementTargetComment:
        """要件定義対象コメントを更新する。"""
        comment.body = comment_in.body
        comment.version += 1
        db.commit()
        db.refresh(comment)
        return comment

    def set_resolved(
        self,
        db: Session,
        *,
        comment: RequirementTargetComment,
        is_resolved: bool,
    ) -> RequirementTargetComment:
        """要件定義対象コメントの解決状態を更新する。"""
        comment.is_resolved = is_resolved
        comment.version += 1
        db.commit()
        db.refresh(comment)
        return comment

    def soft_delete(
        self,
        db: Session,
        *,
        comment: RequirementTargetComment,
    ) -> RequirementTargetComment:
        """要件定義対象コメントを論理削除する。"""
        comment.deleted_at = datetime.now(UTC)
        db.commit()
        db.refresh(comment)
        return comment


class RequirementRepository:
    """Requirementテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        requirement_in: RequirementCreate,
        actor_id: int | None = None,
    ) -> Requirement:
        """要件を作成する。"""
        requirement = Requirement(
            document_id=requirement_in.document_id,
            section_id=requirement_in.section_id,
            requirement_code=requirement_in.requirement_code,
            requirement_type=requirement_in.requirement_type,
            category=requirement_in.category,
            title=requirement_in.title,
            description=requirement_in.description,
            rationale=requirement_in.rationale,
            acceptance_criteria=requirement_in.acceptance_criteria,
            priority=requirement_in.priority,
            status=requirement_in.status,
            source=requirement_in.source,
            owner_id=requirement_in.owner_id,
            approved_by=requirement_in.approved_by,
            approved_at=requirement_in.approved_at,
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(requirement)
        db.commit()
        db.refresh(requirement)
        return requirement

    def get_by_id(self, db: Session, requirement_id: int) -> Requirement | None:
        """idに一致する要件を取得する。"""
        return (
            db.query(Requirement)
            .filter(Requirement.id == requirement_id, Requirement.deleted_at.is_(None))
            .first()
        )

    def get_by_document_requirement_code(
        self,
        db: Session,
        *,
        document_id: int,
        requirement_code: str,
    ) -> Requirement | None:
        """document_id/requirement_codeに一致する要件を取得する。"""
        return (
            db.query(Requirement)
            .filter(
                Requirement.document_id == document_id,
                Requirement.requirement_code == requirement_code,
                Requirement.deleted_at.is_(None),
            )
            .first()
        )

    def list_paginated(
        self,
        db: Session,
        *,
        document_ids: list[int],
        page: int,
        page_size: int,
        q: str | None = None,
        status: str | None = None,
        requirement_type: str | None = None,
        section_id: int | None = None,
    ) -> tuple[list[Requirement], int]:
        """指定要件定義書群の要件一覧をページング付きで取得する。"""
        query = db.query(Requirement).filter(
            Requirement.document_id.in_(document_ids),
            Requirement.deleted_at.is_(None),
        )
        if q:
            like_pattern = f"%{q}%"
            query = query.filter(
                or_(
                    Requirement.requirement_code.ilike(like_pattern),
                    Requirement.title.ilike(like_pattern),
                    Requirement.description.ilike(like_pattern),
                )
            )
        if status is not None:
            query = query.filter(Requirement.status == status)
        if requirement_type is not None:
            query = query.filter(Requirement.requirement_type == requirement_type)
        if section_id is not None:
            query = query.filter(Requirement.section_id == section_id)

        total = query.count()
        requirements = (
            query.order_by(Requirement.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return requirements, total

    def update(
        self,
        db: Session,
        *,
        requirement: Requirement,
        requirement_in: RequirementUpdate,
        actor_id: int | None = None,
    ) -> Requirement:
        """要件を更新する。"""
        for field in (
            "requirement_code",
            "requirement_type",
            "category",
            "title",
            "description",
            "rationale",
            "acceptance_criteria",
            "priority",
            "status",
            "source",
            "owner_id",
            "approved_by",
            "approved_at",
            "section_id",
        ):
            if field in requirement_in.model_fields_set:
                setattr(requirement, field, getattr(requirement_in, field))
        if actor_id is not None:
            requirement.updated_by = actor_id
        requirement.version += 1

        db.flush()
        return requirement

    def soft_delete(
        self,
        db: Session,
        *,
        requirement: Requirement,
        actor_id: int | None = None,
    ) -> Requirement:
        """要件を論理削除する。"""
        requirement.deleted_at = datetime.now(UTC)
        if actor_id is not None:
            requirement.updated_by = actor_id
        db.commit()
        db.refresh(requirement)
        return requirement

    def list_by_document_ids(
        self,
        db: Session,
        document_ids: list[int],
    ) -> list[Requirement]:
        """指定要件定義書群の要件一覧を取得する。"""
        if not document_ids:
            return []
        return (
            db.query(Requirement)
            .filter(
                Requirement.document_id.in_(document_ids),
                Requirement.deleted_at.is_(None),
            )
            .order_by(Requirement.id)
            .all()
        )

    def list_by_document(
        self,
        db: Session,
        document_id: int,
    ) -> list[Requirement]:
        """指定要件定義書の要件一覧を取得する。"""
        return self.list_by_document_ids(db, [document_id])


class RequirementRevisionRepository:
    """RequirementRevisionテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        requirement_id: int,
        version: int,
        changed_by: int | None,
        before_value: dict | None,
        after_value: dict | None,
        change_summary: str | None = None,
        reason: str | None = None,
    ) -> RequirementRevision:
        """要件改訂履歴を作成する。"""
        revision = RequirementRevision(
            requirement_id=requirement_id,
            version=version,
            changed_by=changed_by,
            change_summary=change_summary,
            before_value=before_value,
            after_value=after_value,
            reason=reason,
        )
        db.add(revision)
        db.flush()
        return revision

    def list_by_requirement(
        self,
        db: Session,
        requirement_id: int,
    ) -> list[RequirementRevision]:
        """要件の改訂履歴一覧を取得する。"""
        return (
            db.query(RequirementRevision)
            .filter(RequirementRevision.requirement_id == requirement_id)
            .order_by(RequirementRevision.id)
            .all()
        )

    def list_latest_by_requirement(
        self,
        db: Session,
        requirement_id: int,
        limit: int,
    ) -> list[RequirementRevision]:
        """要件の直近改訂履歴一覧を取得する。"""
        revisions = (
            db.query(RequirementRevision)
            .filter(RequirementRevision.requirement_id == requirement_id)
            .order_by(RequirementRevision.id.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(revisions))


class RequirementDetailRepository:
    """RequirementDetailテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        requirement_id: int,
        detail_in: RequirementDetailCreate,
    ) -> RequirementDetail:
        """要件詳細を作成する。"""
        detail = RequirementDetail(
            requirement_id=requirement_id,
            detail_type=detail_in.detail_type,
            detail_json=detail_in.detail_json,
        )
        db.add(detail)
        db.commit()
        db.refresh(detail)
        return detail

    def list_by_requirement(
        self,
        db: Session,
        requirement_id: int,
    ) -> list[RequirementDetail]:
        """要件詳細一覧を取得する。"""
        return (
            db.query(RequirementDetail)
            .filter(RequirementDetail.requirement_id == requirement_id)
            .order_by(RequirementDetail.id)
            .all()
        )

    def get_by_id(self, db: Session, detail_id: int) -> RequirementDetail | None:
        """idに一致する要件詳細を取得する。"""
        return (
            db.query(RequirementDetail)
            .filter(RequirementDetail.id == detail_id)
            .first()
        )

    def update(
        self,
        db: Session,
        *,
        detail: RequirementDetail,
        detail_in: RequirementDetailUpdate,
    ) -> RequirementDetail:
        """要件詳細を更新する。"""
        if detail_in.detail_type is not None:
            detail.detail_type = detail_in.detail_type
        if detail_in.detail_json is not None:
            detail.detail_json = detail_in.detail_json
        db.commit()
        db.refresh(detail)
        return detail

    def delete(self, db: Session, detail: RequirementDetail) -> None:
        """要件詳細を物理削除する。"""
        db.delete(detail)
        db.commit()


class RequirementLinkRepository:
    """RequirementLinkテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        requirement_id: int,
        link_in: RequirementLinkCreate,
    ) -> RequirementLink:
        """要件リンクを作成する。"""
        link = RequirementLink(
            requirement_id=requirement_id,
            linked_type=link_in.linked_type,
            linked_id=link_in.linked_id,
        )
        db.add(link)
        db.commit()
        db.refresh(link)
        return link

    def list_by_requirement(
        self,
        db: Session,
        requirement_id: int,
    ) -> list[RequirementLink]:
        """要件リンク一覧を取得する。"""
        return (
            db.query(RequirementLink)
            .filter(RequirementLink.requirement_id == requirement_id)
            .order_by(RequirementLink.id)
            .all()
        )

    def get_by_id(self, db: Session, link_id: int) -> RequirementLink | None:
        """idに一致する要件リンクを取得する。"""
        return db.query(RequirementLink).filter(RequirementLink.id == link_id).first()

    def delete(self, db: Session, link: RequirementLink) -> None:
        """要件リンクを物理削除する。"""
        db.delete(link)
        db.commit()


class RequirementCommentRepository:
    """RequirementCommentテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        requirement_id: int,
        user_id: int,
        comment_in: RequirementCommentCreate,
    ) -> RequirementComment:
        """要件コメントを作成する。"""
        comment = RequirementComment(
            requirement_id=requirement_id,
            user_id=user_id,
            comment=comment_in.comment,
        )
        db.add(comment)
        db.commit()
        db.refresh(comment)
        return comment

    def list_by_requirement(
        self,
        db: Session,
        requirement_id: int,
    ) -> list[RequirementComment]:
        """要件コメント一覧を取得する。"""
        return (
            db.query(RequirementComment)
            .filter(RequirementComment.requirement_id == requirement_id)
            .order_by(RequirementComment.id)
            .all()
        )

    def list_latest_by_requirement(
        self,
        db: Session,
        requirement_id: int,
        limit: int,
    ) -> list[RequirementComment]:
        """要件の直近コメント一覧を取得する。"""
        comments = (
            db.query(RequirementComment)
            .filter(RequirementComment.requirement_id == requirement_id)
            .order_by(RequirementComment.id.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(comments))

    def get_by_id(self, db: Session, comment_id: int) -> RequirementComment | None:
        """idに一致する要件コメントを取得する。"""
        return (
            db.query(RequirementComment)
            .filter(RequirementComment.id == comment_id)
            .first()
        )

    def delete(self, db: Session, comment: RequirementComment) -> None:
        """要件コメントを物理削除する。"""
        db.delete(comment)
        db.commit()


class RequirementReviewRepository:
    """RequirementReviewテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        requirement_id: int,
        review_in: RequirementReviewCreate,
    ) -> RequirementReview:
        """要件レビューを作成する。"""
        review = RequirementReview(
            requirement_id=requirement_id,
            reviewer_id=review_in.reviewer_id,
            status=review_in.status,
            comment=review_in.comment,
            reviewed_at=review_in.reviewed_at,
        )
        db.add(review)
        db.commit()
        db.refresh(review)
        return review

    def list_by_requirement(
        self,
        db: Session,
        requirement_id: int,
    ) -> list[RequirementReview]:
        """要件レビュー一覧を取得する。"""
        return (
            db.query(RequirementReview)
            .filter(RequirementReview.requirement_id == requirement_id)
            .order_by(RequirementReview.id)
            .all()
        )

    def get_by_id(self, db: Session, review_id: int) -> RequirementReview | None:
        """idに一致する要件レビューを取得する。"""
        return (
            db.query(RequirementReview)
            .filter(RequirementReview.id == review_id)
            .first()
        )

    def update(
        self,
        db: Session,
        *,
        review: RequirementReview,
        review_in: RequirementReviewUpdate,
    ) -> RequirementReview:
        """要件レビューを更新する。"""
        for field in ("reviewer_id", "status", "comment", "reviewed_at"):
            if field in review_in.model_fields_set:
                setattr(review, field, getattr(review_in, field))
        db.commit()
        db.refresh(review)
        return review

    def delete(self, db: Session, review: RequirementReview) -> None:
        """要件レビューを物理削除する。"""
        db.delete(review)
        db.commit()
