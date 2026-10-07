"""文書の本文、版と同一Project内の参照を管理する。"""

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.db.demo_scope import lock_active_demo
from app.models.document import (
    DocumentAttachment,
    DocumentLink,
    DocumentRevision,
    ProjectDocument,
)
from app.models.user import User
from app.repositories.document import DocumentRepository
from app.schemas.document import (
    DocumentCreate,
    DocumentLinkCreate,
    DocumentLinkRead,
    DocumentLinkTarget,
    DocumentList,
    DocumentListItem,
    DocumentRead,
    DocumentTargetType,
    DocumentUpdate,
)
from app.services.audit_log import AuditLogService
from app.services.authorization import AuthorizationService
from app.services.conflict import raise_if_version_conflict

TARGET_PERMISSIONS = {
    DocumentTargetType.REQUIREMENT: "requirement:read",
    DocumentTargetType.TASK: "task:read",
    DocumentTargetType.TEST_DESIGN: "test_plan:read",
}
TARGET_COLUMNS = {
    DocumentTargetType.REQUIREMENT: "requirement_id",
    DocumentTargetType.TASK: "task_id",
    DocumentTargetType.TEST_DESIGN: "design_id",
}


class DocumentService:
    """本文の変更を版と原子的に保存し、既存RBACを関連先にも適用する。"""

    def __init__(self, repository: DocumentRepository | None = None) -> None:
        """既存の永続化と監査部品を利用する。"""
        self.repository = repository or DocumentRepository()
        self.authorization = AuthorizationService()
        self.audit = AuditLogService()

    def get(
        self, db: Session, project_id: int, document_id: int, *, lock: bool = False
    ) -> ProjectDocument:
        """Projectと文書の両方の有効性を確認する。"""
        if lock and db.info.get("demo_id"):
            # 回収もデモ行からロックする。同じ順序でdeadlockを防ぐ。
            lock_active_demo(db)
        if not self.repository.project_exists(db, project_id):
            raise NotFoundError(error_messages.PROJECT_NOT_FOUND)
        row = self.repository.get(db, project_id, document_id, lock=lock)
        if row is None:
            raise NotFoundError(error_messages.DOCUMENT_NOT_FOUND)
        return row

    def list(
        self, db: Session, project_id: int, page: int, page_size: int, q: str | None
    ) -> DocumentList:
        """有効なProjectの文書を本文なしでページングする。"""
        if not self.repository.project_exists(db, project_id):
            raise NotFoundError(error_messages.PROJECT_NOT_FOUND)
        rows, total = self.repository.list(db, project_id, page, page_size, q)
        return DocumentList(
            items=[DocumentListItem.model_validate(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    def revisions(
        self, db: Session, project_id: int, document_id: int
    ) -> list[DocumentRevision]:
        """取得可能な文書の版概要を返す。"""
        self.get(db, project_id, document_id)
        return self.repository.revisions(db, document_id)

    def revision(
        self, db: Session, project_id: int, document_id: int, number: int
    ) -> DocumentRevision:
        """取得可能な文書の指定版を返す。"""
        self.get(db, project_id, document_id)
        row = self.repository.revision(db, document_id, number)
        if row is None:
            raise NotFoundError(error_messages.DOCUMENT_NOT_FOUND)
        return row

    def attachments(
        self, db: Session, project_id: int, document_id: int
    ) -> list[DocumentAttachment]:
        """取得可能な文書の有効な添付を返す。"""
        self.get(db, project_id, document_id)
        return self.repository.attachments(db, document_id)

    def create(
        self, db: Session, project_id: int, data: DocumentCreate, actor_id: int
    ) -> ProjectDocument:
        """初期本文と版1を保存する。"""
        if not self.repository.project_exists(db, project_id):
            raise NotFoundError(error_messages.PROJECT_NOT_FOUND)
        row = self.repository.create(db, project_id, data.title, data.body, actor_id)
        db.commit()
        db.refresh(row)
        self.record(db, row, actor_id, "created")
        return row

    def update(
        self,
        db: Session,
        project_id: int,
        document_id: int,
        data: DocumentUpdate,
        actor_id: int,
    ) -> ProjectDocument:
        """競合を拒否し、変更があるときだけ新しい本文版を追加する。"""
        row = self.get(db, project_id, document_id, lock=True)
        self.check_version(row, data.version)
        if (row.title, row.body) == (data.title, data.body):
            return row
        row.title, row.body = data.title, data.body
        row.version += 1
        row.updated_by = actor_id
        self.repository.append_revision(db, row, actor_id)
        db.commit()
        db.refresh(row)
        self.record(db, row, actor_id, "updated")
        return row

    def delete(
        self,
        db: Session,
        project_id: int,
        document_id: int,
        version: int,
        actor_id: int,
    ) -> None:
        """本文と添付の取得経路を閉じ、復元用の既存行は保持する。"""
        row = self.get(db, project_id, document_id, lock=True)
        self.check_version(row, version)
        row.deleted_at = datetime.now(UTC)
        row.version += 1
        row.updated_by = actor_id
        db.commit()
        self.record(db, row, actor_id, "deleted")

    def check_version(self, row: ProjectDocument, version: int) -> None:
        """現在の本文を含む既存形式の409応答を使う。"""
        raise_if_version_conflict(
            current_version=row.version,
            requested_version=version,
            current=DocumentRead.model_validate(row).model_dump(mode="json"),
        )

    def record(
        self, db: Session, row: ProjectDocument, actor_id: int, action: str
    ) -> None:
        """本文や署名URLを監査ログへ含めない。"""
        self.audit.record(
            db,
            event_type=f"document.{action}",
            actor_user_id=actor_id,
            project_id=row.project_id,
            resource_type="document",
            resource_id=row.id,
            metadata={"version": row.version},
        )

    def candidates(
        self,
        db: Session,
        project_id: int,
        kind: DocumentTargetType,
        user: User,
        q: str | None = None,
    ) -> list[DocumentLinkTarget]:
        """参照先の閲覧権限を要求し、候補は50件に制限する。"""
        if not self.authorization.has_project_permission(
            db,
            user=user,
            project_id=project_id,
            permission_code=TARGET_PERMISSIONS[kind],
        ):
            raise ForbiddenError()
        return [
            DocumentLinkTarget(
                target_type=kind,
                target_id=target_id,
                title=title,
                requirement_document_id=parent_id,
            )
            for target_id, title, parent_id in self.repository.target_rows(
                db, project_id, kind, q=q
            )
        ]

    def links(
        self, db: Session, project_id: int, document_id: int, user: User
    ) -> list[DocumentLinkRead]:
        """閲覧できない関連先のタイトルは返さない。"""
        self.get(db, project_id, document_id)
        result = []
        for row in self.repository.links(db, document_id):
            kind = next(
                kind
                for kind, column in TARGET_COLUMNS.items()
                if getattr(row, column) is not None
            )
            target_id = getattr(row, TARGET_COLUMNS[kind])
            targets = []
            if self.authorization.has_project_permission(
                db,
                user=user,
                project_id=project_id,
                permission_code=TARGET_PERMISSIONS[kind],
            ):
                targets = self.repository.target_rows(
                    db, project_id, kind, target_id=target_id
                )
            result.append(
                DocumentLinkRead(
                    id=row.id,
                    target_type=kind,
                    target_id=target_id,
                    title=targets[0][1] if targets else None,
                    requirement_document_id=targets[0][2] if targets else None,
                )
            )
        return result

    def add_link(
        self,
        db: Session,
        project_id: int,
        document_id: int,
        data: DocumentLinkCreate,
        user: User,
    ) -> list[DocumentLinkRead]:
        """同一Projectで閲覧可能な対象だけを関連付ける。"""
        document = self.get(db, project_id, document_id, lock=True)
        if not self.authorization.has_project_permission(
            db,
            user=user,
            project_id=project_id,
            permission_code=TARGET_PERMISSIONS[data.target_type],
        ):
            raise ForbiddenError()
        if not self.repository.target_rows(
            db, project_id, data.target_type, target_id=data.target_id
        ):
            raise NotFoundError(error_messages.DOCUMENT_TARGET_NOT_FOUND)
        column = TARGET_COLUMNS[data.target_type]
        rows = self.repository.links(db, document_id)
        if any(getattr(row, column) == data.target_id for row in rows):
            return self.links(db, project_id, document_id, user)
        if len(rows) >= 50:
            raise BadRequestError(error_messages.DOCUMENT_LINK_LIMIT)
        row = DocumentLink(
            document_id=document_id, created_by=user.id, **{column: data.target_id}
        )
        try:
            self.repository.add_link(db, row)
            db.commit()
        except IntegrityError:
            db.rollback()
            raise BadRequestError(error_messages.DOCUMENT_TARGET_NOT_FOUND) from None
        self.record(db, document, user.id, "link_added")
        return self.links(db, project_id, document_id, user)

    def delete_link(
        self,
        db: Session,
        project_id: int,
        document_id: int,
        link_id: int,
        actor_id: int,
    ) -> None:
        """現在文書に属する関連だけを解除する。"""
        document = self.get(db, project_id, document_id, lock=True)
        row = next(
            (
                row
                for row in self.repository.links(db, document_id)
                if row.id == link_id
            ),
            None,
        )
        if row is None:
            raise NotFoundError(error_messages.DOCUMENT_TARGET_NOT_FOUND)
        self.repository.delete_link(db, row)
        db.commit()
        self.record(db, document, actor_id, "link_removed")
