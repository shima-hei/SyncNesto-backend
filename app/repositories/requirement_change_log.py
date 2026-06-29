"""要件定義Repositoryを定義するモジュール。"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.requirement import (
    RequirementChangeLog,
)


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
        changed_by: int | None = None,
        changed_at_from: datetime | None = None,
        changed_at_to: datetime | None = None,
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
        if changed_by is not None:
            query = query.filter(RequirementChangeLog.changed_by == changed_by)
        if changed_at_from is not None:
            query = query.filter(RequirementChangeLog.changed_at >= changed_at_from)
        if changed_at_to is not None:
            query = query.filter(RequirementChangeLog.changed_at <= changed_at_to)

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
