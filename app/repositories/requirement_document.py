"""要件定義Repositoryを定義するモジュール。"""

from datetime import UTC, datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.requirement import (
    RequirementDocument,
)
from app.schemas.requirement import (
    RequirementDocumentCreate,
    RequirementDocumentUpdate,
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

    def list_by_ids(
        self,
        db: Session,
        document_ids: list[int],
    ) -> list[RequirementDocument]:
        """id一覧に一致する要件定義書一覧を取得する。"""
        if not document_ids:
            return []

        return (
            db.query(RequirementDocument)
            .filter(
                RequirementDocument.id.in_(document_ids),
                RequirementDocument.deleted_at.is_(None),
            )
            .order_by(RequirementDocument.id)
            .all()
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
