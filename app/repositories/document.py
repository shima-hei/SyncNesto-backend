"""Project文書の永続化。commitと業務検証はServiceが管理する。"""

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, load_only

from app.models.document import (
    DocumentAttachment,
    DocumentLink,
    DocumentRevision,
    ProjectDocument,
)
from app.models.project import Project
from app.models.requirement import Requirement, RequirementDocument
from app.models.task import Task
from app.models.test_design import TestDesign
from app.schemas.document import DocumentTargetType


class DocumentRepository:
    """所有Projectと論理削除を明示して取得する。"""

    def project_exists(self, db: Session, project_id: int) -> bool:
        """有効な所属Projectの存在を確認する。"""
        return (
            db.scalar(
                select(Project.id).where(
                    Project.id == project_id, Project.deleted_at.is_(None)
                )
            )
            is not None
        )

    def create(
        self, db: Session, project_id: int, title: str, body: str, actor_id: int
    ) -> ProjectDocument:
        """文書と初期版を同じtransactionへ追加する。"""
        row = ProjectDocument(
            project_id=project_id,
            title=title,
            body=body,
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(row)
        db.flush()
        self.append_revision(db, row, actor_id)
        return row

    def append_revision(self, db: Session, row: ProjectDocument, actor_id: int) -> None:
        """現在の本文を不変の版として追加する。"""
        db.add(
            DocumentRevision(
                document_id=row.id,
                number=row.version,
                title=row.title,
                body=row.body,
                created_by=actor_id,
            )
        )

    def add_attachment(self, db: Session, row: DocumentAttachment) -> None:
        """検証済みの確定ファイルを登録する。"""
        db.add(row)

    def add_link(self, db: Session, row: DocumentLink) -> None:
        """検証済み参照を登録する。"""
        db.add(row)

    def delete_link(self, db: Session, row: DocumentLink) -> None:
        """参照だけを解除し、対象資源は保持する。"""
        db.delete(row)

    def get(
        self, db: Session, project_id: int, document_id: int, *, lock: bool = False
    ) -> ProjectDocument | None:
        """文書を取得し、更新時には行をロックする。"""
        statement = select(ProjectDocument).where(
            ProjectDocument.id == document_id,
            ProjectDocument.project_id == project_id,
            ProjectDocument.deleted_at.is_(None),
        )
        if lock:
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        return db.scalar(statement)

    def list(
        self, db: Session, project_id: int, page: int, page_size: int, q: str | None
    ) -> tuple[list[ProjectDocument], int]:
        """タイトルと本文を文字列で検索し、更新が新しい順に返す。"""
        conditions = [
            ProjectDocument.project_id == project_id,
            ProjectDocument.deleted_at.is_(None),
        ]
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(
                or_(
                    ProjectDocument.title.ilike(f"%{escaped}%", escape="\\"),
                    ProjectDocument.body.ilike(f"%{escaped}%", escape="\\"),
                )
            )
        total = (
            db.scalar(
                select(func.count()).select_from(ProjectDocument).where(*conditions)
            )
            or 0
        )
        rows = db.scalars(
            select(ProjectDocument)
            .options(
                load_only(
                    ProjectDocument.id,
                    ProjectDocument.title,
                    ProjectDocument.version,
                    ProjectDocument.updated_at,
                )
            )
            .where(*conditions)
            .order_by(ProjectDocument.updated_at.desc(), ProjectDocument.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows), total

    def revisions(self, db: Session, document_id: int) -> list[DocumentRevision]:
        """本文の版一覧を最新から返す。"""
        return list(
            db.scalars(
                select(DocumentRevision)
                .options(
                    load_only(
                        DocumentRevision.id,
                        DocumentRevision.number,
                        DocumentRevision.title,
                        DocumentRevision.created_at,
                    )
                )
                .where(DocumentRevision.document_id == document_id)
                .order_by(DocumentRevision.number.desc())
                .limit(100)
            )
        )

    def revision(
        self, db: Session, document_id: int, number: int
    ) -> DocumentRevision | None:
        """親文書に属する指定版だけを返す。"""
        return db.scalar(
            select(DocumentRevision).where(
                DocumentRevision.document_id == document_id,
                DocumentRevision.number == number,
            )
        )

    def attachments(self, db: Session, document_id: int) -> list[DocumentAttachment]:
        """有効な添付一覧を返す。"""
        return list(
            db.scalars(
                select(DocumentAttachment)
                .where(
                    DocumentAttachment.document_id == document_id,
                    DocumentAttachment.deleted_at.is_(None),
                )
                .order_by(DocumentAttachment.created_at, DocumentAttachment.id)
            )
        )

    def attachment(
        self, db: Session, document_id: int, attachment_id: UUID
    ) -> DocumentAttachment | None:
        """削除された添付も含め、完了要求の再送を判別する。"""
        return db.scalar(
            select(DocumentAttachment).where(
                DocumentAttachment.document_id == document_id,
                DocumentAttachment.id == attachment_id,
            )
        )

    def links(self, db: Session, document_id: int) -> list[DocumentLink]:
        """文書に登録された参照を返す。"""
        return list(
            db.scalars(
                select(DocumentLink)
                .where(DocumentLink.document_id == document_id)
                .order_by(DocumentLink.id)
            )
        )

    def target_rows(
        self,
        db: Session,
        project_id: int,
        kind: DocumentTargetType,
        *,
        target_id: int | None = None,
        q: str | None = None,
    ) -> list[tuple[int, str, int | None]]:
        """同じProject内の有効な対象だけを検索する。"""
        if kind == DocumentTargetType.REQUIREMENT:
            model = Requirement
            title = Requirement.title
            statement = (
                select(model.id, model.title, model.document_id)
                .join(RequirementDocument, RequirementDocument.id == model.document_id)
                .where(
                    RequirementDocument.project_id == project_id,
                    RequirementDocument.deleted_at.is_(None),
                )
            )
        elif kind == DocumentTargetType.TASK:
            model = Task
            title = Task.title
            statement = select(model.id, model.title).where(
                model.project_id == project_id
            )
        else:
            model = TestDesign
            title = TestDesign.name
            statement = select(model.id, model.name).where(
                model.project_id == project_id
            )
        statement = statement.where(model.deleted_at.is_(None))
        if target_id is not None:
            statement = statement.where(model.id == target_id)
        if q:
            statement = statement.where(title.contains(q, autoescape=True))
        rows = db.execute(statement.order_by(model.id).limit(50))
        return [(row[0], row[1], row[2] if len(row) == 3 else None) for row in rows]
